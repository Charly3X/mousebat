"""Locating Logitech receivers and the battery-bearing devices paired with them.

A receiver speaks HID++ on only one of its hidraw nodes — the interface whose
report descriptor declares reports 0x10/0x11. The remaining nodes are plain
mouse/keyboard interfaces and never answer requests.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass

from . import hidpp

SYS_HIDRAW = "/sys/class/hidraw"
LOGITECH_VENDOR = 0x046D

FUNC_NAME_GET_COUNT = 0x0
FUNC_NAME_GET_NAME = 0x1
FUNC_NAME_GET_TYPE = 0x2

DEVICE_TYPE_KEYBOARD = 0x00
DEVICE_TYPE_MOUSE = 0x03
DEVICE_TYPE_TRACKBALL = 0x05

#: Device types worth a tray item. A receiver (0x07) has no battery of its own.
BATTERY_DEVICE_TYPES = frozenset(
    {DEVICE_TYPE_KEYBOARD, DEVICE_TYPE_MOUSE, DEVICE_TYPE_TRACKBALL}
)

DEVICE_INDICES = range(1, 7)

_HID_ID = re.compile(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{8}):([0-9a-fA-F]{8})$")


@dataclass(frozen=True)
class ReceiverNode:
    """A hidraw node on which a receiver accepts HID++."""

    device_path: str
    product_id: int


@dataclass(frozen=True)
class HidppDevice:
    """A battery-bearing device found behind a receiver."""

    device_path: str
    device_index: int
    name: str
    protocol: tuple[int, int]
    kind: int


def parse_hid_id(hid_id: str) -> tuple[int, int] | None:
    """`0003:0000046D:0000C548` -> (0x046D, 0xC548)."""
    match = _HID_ID.match(hid_id.strip())
    if match is None:
        return None
    return int(match.group(2), 16), int(match.group(3), 16)


def speaks_hidpp(report_descriptor: bytes) -> bool:
    """Whether the interface declares long report 0x11 — the mark of a HID++ channel."""
    return b"\x85\x11" in report_descriptor


def _read_uevent(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                key, _, value = line.partition("=")
                if value:
                    values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def _hidraw_sort_key(name: str) -> tuple[int, str]:
    digits = "".join(ch for ch in name if ch.isdigit())
    return (int(digits) if digits else 1 << 30, name)


def is_paired_device_node(device_dir: str) -> bool:
    """Whether this hidraw node belongs to a paired device rather than a receiver.

    A receiver's HID device hangs off a USB interface; a paired device's hangs off the
    receiver's own HID device. Only the latter has a HID parent, and a HID device is
    recognisable by its report descriptor. Verified on the target machine:

        hidraw4 -> .../3-4:1.2/0003:046D:C52B.0007               parent 3-4:1.2
        hidraw5 -> .../0003:046D:C52B.0007/0003:046D:408A.0008   parent 0003:046D:C52B...

    Both kinds answer HID++, so without this the same device is reported twice — once
    through its receiver and once through its own node.
    """
    parent = os.path.dirname(os.path.realpath(device_dir))
    return os.path.exists(os.path.join(parent, "report_descriptor"))


def find_receivers(sys_hidraw: str = SYS_HIDRAW, dev_root: str = "/dev") -> list[ReceiverNode]:
    """Every Logitech hidraw node ready to speak HID++."""
    try:
        names = sorted(os.listdir(sys_hidraw), key=_hidraw_sort_key)
    except OSError:
        return []

    found: list[ReceiverNode] = []
    for name in names:
        device_dir = os.path.join(sys_hidraw, name, "device")
        ids = parse_hid_id(_read_uevent(os.path.join(device_dir, "uevent")).get("HID_ID", ""))
        if ids is None or ids[0] != LOGITECH_VENDOR:
            continue
        try:
            with open(os.path.join(device_dir, "report_descriptor"), "rb") as handle:
                descriptor = handle.read()
        except OSError:
            continue
        if not speaks_hidpp(descriptor):
            continue
        if is_paired_device_node(device_dir):
            continue
        found.append(ReceiverNode(device_path=os.path.join(dev_root, name), product_id=ids[1]))
    return found


def device_type(link: hidpp.Link, device_index: int, name_feature: int) -> int:
    params = link.request(device_index, name_feature, FUNC_NAME_GET_TYPE)
    return params[0] if params else 0xFF


def device_name(link: hidpp.Link, device_index: int, name_feature: int) -> str:
    """Assemble the name from its 16-byte chunks."""
    count = link.request(device_index, name_feature, FUNC_NAME_GET_COUNT)
    length = count[0] if count else 0
    chunks: list[bytes] = []
    offset = 0
    while offset < length:
        params = link.request(
            device_index, name_feature, FUNC_NAME_GET_NAME, bytes((offset,))
        )
        if not params:
            break
        chunks.append(params)
        offset += len(params)
    raw = b"".join(chunks)[:length]
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def probe_devices(link: hidpp.Link, device_path: str) -> list[HidppDevice]:
    """Walk indices 1..6 and return every device that carries a battery."""
    devices: list[HidppDevice] = []
    for index in DEVICE_INDICES:
        try:
            protocol = link.ping(index)
        except (hidpp.HidppTimeout, hidpp.HidppError, hidpp.DeviceNotConnected):
            continue
        try:
            name_feature = link.feature_index(index, hidpp.FEATURE_DEVICE_NAME)
            if name_feature is None:
                continue
            kind = device_type(link, index, name_feature)
            if kind not in BATTERY_DEVICE_TYPES:
                continue
            name = device_name(link, index, name_feature)
        except (hidpp.HidppTimeout, hidpp.HidppError, hidpp.DeviceNotConnected):
            continue
        devices.append(
            HidppDevice(
                device_path=device_path,
                device_index=index,
                name=name or "Device",
                protocol=protocol,
                kind=kind,
            )
        )
    return devices


#: Serialises the index walk across threads.
#:
#: `software_id` tells our replies apart from another program's, but not one of our
#: own threads from another: two walks at once ask the same indices the same questions
#: with the same id, and each reads the other's answers. Observed on the target
#: machine — a name came back spliced together as "MX Keys WirelessMX Keys W", which
#: then failed to match its own device by identity. One walk at a time.
_WALK = threading.Lock()


def find_devices(
    sys_hidraw: str = SYS_HIDRAW, dev_root: str = "/dev", *, timeout: float = 0.5
) -> list[HidppDevice]:
    """Every device behind every receiver, in walk order.

    Receivers by hidraw number, then by `device_index` — the order the tray items
    will end up in.
    """
    with _WALK:
        devices: list[HidppDevice] = []
        for receiver in find_receivers(sys_hidraw, dev_root):
            try:
                with hidpp.Transport(receiver.device_path) as transport:
                    link = hidpp.Link(transport, timeout=timeout)
                    devices.extend(probe_devices(link, receiver.device_path))
            except OSError:
                continue
        return devices


def relocate(
    device: HidppDevice,
    sys_hidraw: str = SYS_HIDRAW,
    dev_root: str = "/dev",
    *,
    timeout: float = 0.5,
) -> HidppDevice | None:
    """Find a known device again, wherever its hidraw node has moved to.

    Replugging a receiver renumbers the nodes, so a stored path goes stale while the
    device itself is unchanged. Identity is what survives: name, index and type.
    """
    identity = (device.name, device.device_index, device.kind)
    for candidate in find_devices(sys_hidraw, dev_root, timeout=timeout):
        if (candidate.name, candidate.device_index, candidate.kind) == identity:
            return candidate
    return None
