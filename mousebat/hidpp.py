"""HID++ 2.0 transport over /dev/hidraw.

This module only knows about packets and their delivery: nothing about batteries,
mice or Qt. Every request carries our software_id so we can tell our own replies
apart from foreign ones — logid works on the same hidraw node, and the kernel
broadcasts incoming reports to every open descriptor at once.
"""

from __future__ import annotations

import os
import select
import time
from dataclasses import dataclass

SHORT_REPORT_ID = 0x10
LONG_REPORT_ID = 0x11
ERROR_REPORT_ID = 0xFF  # HID++ 2.0 errors arrive under this report id
SHORT_LEN = 7
LONG_LEN = 20

#: Our software_id (4 bits, 1..15 allowed). Separates our replies from logid's.
SOFTWARE_ID = 0x0E

RECEIVER_INDEX = 0xFF

ROOT_FEATURE_INDEX = 0x00
FEATURE_ROOT = 0x0000
FEATURE_DEVICE_NAME = 0x0005
FEATURE_BATTERY_STATUS = 0x1000
FEATURE_UNIFIED_BATTERY = 0x1004

FUNC_ROOT_GET_FEATURE = 0x0
FUNC_ROOT_PING = 0x1

PING_MARKER = 0xAA

SUBID_ERROR_HIDPP10 = 0x8F

#: HID++ 1.0 codes meaning "no device on this index, or no link to it".
HIDPP10_ABSENT_CODES = frozenset({0x08, 0x09})

HIDPP20_ERRORS = {
    0x00: "no error",
    0x01: "unknown",
    0x02: "invalid argument",
    0x03: "out of range",
    0x04: "hardware error",
    0x05: "logitech internal",
    0x06: "invalid feature index",
    0x07: "invalid function id",
    0x08: "busy",
    0x09: "unsupported",
}

HIDPP10_ERRORS = {
    0x01: "invalid sub id",
    0x02: "invalid address",
    0x03: "invalid value",
    0x04: "connection request failed",
    0x05: "too many devices",
    0x06: "already exists",
    0x07: "busy",
    0x08: "unknown device",
    0x09: "resource error",
    0x0A: "request unavailable",
    0x0B: "unsupported parameter value",
    0x0C: "wrong pin code",
}


class HidppError(Exception):
    """The device answered with an error code."""

    def __init__(self, code: int, *, protocol: int, message: str) -> None:
        super().__init__(f"HID++ {protocol}.0 error 0x{code:02X}: {message}")
        self.code = code
        self.protocol = protocol


class HidppTimeout(Exception):
    """No reply carrying our software_id arrived in time."""


class DeviceNotConnected(Exception):
    """Nothing is paired on this device_index, or the device is asleep."""


@dataclass(frozen=True)
class Response:
    report_id: int
    device_index: int
    feature_index: int
    address: int
    params: bytes

    @property
    def function(self) -> int:
        return self.address >> 4

    @property
    def software_id(self) -> int:
        return self.address & 0x0F


def parse(raw: bytes) -> Response | None:
    """Decode a raw report. None if it does not look like a HID++ packet.

    The layout is the same for all three report kinds, including the 0xFF error
    report: an error carries the originating request's fields in feature_index
    and address, with params[0] holding the error code.
    """
    if len(raw) < 4 or raw[0] not in (SHORT_REPORT_ID, LONG_REPORT_ID, ERROR_REPORT_ID):
        return None
    return Response(
        report_id=raw[0],
        device_index=raw[1],
        feature_index=raw[2],
        address=raw[3],
        params=raw[4:],
    )


def build(device_index: int, feature_index: int, function: int, params: bytes = b"") -> bytes:
    """Assemble a short or long request; the length follows the parameter size."""
    if len(params) > LONG_LEN - 4:
        raise ValueError(f"too many params: {len(params)}")
    report_id = SHORT_REPORT_ID if len(params) <= SHORT_LEN - 4 else LONG_REPORT_ID
    total = SHORT_LEN if report_id == SHORT_REPORT_ID else LONG_LEN
    address = (function << 4) | SOFTWARE_ID
    head = bytes((report_id, device_index, feature_index, address))
    return head + params.ljust(total - 4, b"\x00")


class Transport:
    """An open /dev/hidraw node: a thin wrapper around read/write with a timeout."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def write(self, data: bytes) -> None:
        os.write(self._fd, data)

    def read(self, timeout: float) -> bytes | None:
        """Read one report, or return None once timeout expires."""
        if timeout <= 0:
            return None
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        return os.read(self._fd, LONG_LEN)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self) -> Transport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Link:
    """A HID++ conversation over one hidraw node.

    Anything that does not answer our request is dropped: a foreign software_id
    (logid), another device_index, another feature. That is what lets two clients
    share a single node.
    """

    def __init__(self, transport: Transport, *, timeout: float = 0.5) -> None:
        self.transport = transport
        self.timeout = timeout

    def request(
        self,
        device_index: int,
        feature_index: int,
        function: int,
        params: bytes = b"",
        *,
        timeout: float | None = None,
    ) -> bytes:
        """Send a request and return the reply parameters."""
        budget = self.timeout if timeout is None else timeout
        self.transport.write(build(device_index, feature_index, function, params))
        return self._await_reply(device_index, feature_index, function, budget)

    def _await_reply(
        self, device_index: int, feature_index: int, function: int, budget: float
    ) -> bytes:
        expected_address = (function << 4) | SOFTWARE_ID
        deadline = time.monotonic() + budget
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            raw = self.transport.read(remaining)
            if raw is None:
                break
            reply = parse(raw)
            if reply is None or reply.device_index != device_index:
                continue
            error = self._as_error(reply, feature_index, expected_address)
            if error is not None:
                raise error
            if (
                reply.report_id != ERROR_REPORT_ID
                and reply.feature_index == feature_index
                and reply.address == expected_address
            ):
                return reply.params
        raise HidppTimeout(
            f"no reply for feature 0x{feature_index:02X} function 0x{function:X} "
            f"on device {device_index}"
        )

    @staticmethod
    def _as_error(reply: Response, feature_index: int, expected_address: int) -> Exception | None:
        """Recognise an error reply of either protocol; None otherwise."""
        if reply.report_id == ERROR_REPORT_ID:
            # 0xFF, index, original feature, original address, code
            if reply.feature_index != feature_index or reply.address != expected_address:
                return None
            if not reply.params:
                return None
            code = reply.params[0]
            return HidppError(code, protocol=2, message=HIDPP20_ERRORS.get(code, "unknown"))

        if reply.feature_index == SUBID_ERROR_HIDPP10:
            # 0x10, index, 0x8F, original sub id, original address, code
            if len(reply.params) < 2:
                return None
            if reply.address != feature_index or reply.params[0] != expected_address:
                return None
            code = reply.params[1]
            if code in HIDPP10_ABSENT_CODES:
                return DeviceNotConnected(
                    f"device {reply.device_index}: {HIDPP10_ERRORS.get(code, 'absent')}"
                )
            return HidppError(code, protocol=1, message=HIDPP10_ERRORS.get(code, "unknown"))

        return None

    def ping(self, device_index: int, *, attempts: int = 3) -> tuple[int, int]:
        """Return the protocol version (major, minor). A sleeping mouse may miss the first try."""
        last: Exception | None = None
        for _ in range(attempts):
            try:
                params = self.request(
                    device_index,
                    ROOT_FEATURE_INDEX,
                    FUNC_ROOT_PING,
                    bytes((0x00, 0x00, PING_MARKER)),
                )
            except (HidppTimeout, HidppError) as exc:
                last = exc
                continue
            if len(params) >= 3 and params[2] == PING_MARKER:
                return params[0], params[1]
            last = HidppTimeout(f"ping echo mismatch on device {device_index}")
        raise last if last is not None else HidppTimeout("ping failed")

    def feature_index(self, device_index: int, feature_id: int) -> int | None:
        """The feature's index, or None when the device does not support it."""
        params = self.request(
            device_index,
            ROOT_FEATURE_INDEX,
            FUNC_ROOT_GET_FEATURE,
            bytes(((feature_id >> 8) & 0xFF, feature_id & 0xFF, 0x00)),
        )
        index = params[0] if params else 0
        return index or None
