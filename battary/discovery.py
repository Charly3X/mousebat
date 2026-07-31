"""Поиск Logitech-ресиверов и подключённых к ним мышей.

Ресивер отдаёт HID++ не на всех своих hidraw-узлах, а только на том интерфейсе,
чей report descriptor объявляет отчёты 0x10/0x11. Остальные узлы — обычные
mouse/keyboard-интерфейсы, на запросы они не отвечают.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import hidpp

SYS_HIDRAW = "/sys/class/hidraw"
LOGITECH_VENDOR = 0x046D

FUNC_NAME_GET_COUNT = 0x0
FUNC_NAME_GET_NAME = 0x1
FUNC_NAME_GET_TYPE = 0x2

DEVICE_TYPE_MOUSE = 0x03
DEVICE_TYPE_TRACKBALL = 0x05
POINTER_TYPES = frozenset({DEVICE_TYPE_MOUSE, DEVICE_TYPE_TRACKBALL})

DEVICE_INDICES = range(1, 7)

_HID_ID = re.compile(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{8}):([0-9a-fA-F]{8})$")


@dataclass(frozen=True)
class ReceiverNode:
    """hidraw-узел, на котором ресивер принимает HID++."""

    device_path: str
    product_id: int


@dataclass(frozen=True)
class MouseDevice:
    """Мышь, найденная за ресивером."""

    device_path: str
    device_index: int
    name: str
    protocol: tuple[int, int]


def parse_hid_id(hid_id: str) -> tuple[int, int] | None:
    """`0003:0000046D:0000C548` -> (0x046D, 0xC548)."""
    match = _HID_ID.match(hid_id.strip())
    if match is None:
        return None
    return int(match.group(2), 16), int(match.group(3), 16)


def speaks_hidpp(report_descriptor: bytes) -> bool:
    """Объявляет ли интерфейс long-отчёт 0x11 — признак HID++-канала."""
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


def find_receivers(sys_hidraw: str = SYS_HIDRAW, dev_root: str = "/dev") -> list[ReceiverNode]:
    """Все hidraw-узлы Logitech, готовые говорить на HID++."""
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
        found.append(ReceiverNode(device_path=os.path.join(dev_root, name), product_id=ids[1]))
    return found


def device_type(link: hidpp.Link, device_index: int, name_feature: int) -> int:
    params = link.request(device_index, name_feature, FUNC_NAME_GET_TYPE)
    return params[0] if params else 0xFF


def device_name(link: hidpp.Link, device_index: int, name_feature: int) -> str:
    """Собрать имя из 16-байтовых чанков."""
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


def probe_mice(link: hidpp.Link, device_path: str) -> list[MouseDevice]:
    """Пройти индексы 1..6 и вернуть те, что оказались указывающими устройствами."""
    mice: list[MouseDevice] = []
    for index in DEVICE_INDICES:
        try:
            protocol = link.ping(index)
        except (hidpp.HidppTimeout, hidpp.HidppError, hidpp.DeviceNotConnected):
            continue
        try:
            name_feature = link.feature_index(index, hidpp.FEATURE_DEVICE_NAME)
            if name_feature is None:
                continue
            if device_type(link, index, name_feature) not in POINTER_TYPES:
                continue
            name = device_name(link, index, name_feature)
        except (hidpp.HidppTimeout, hidpp.HidppError, hidpp.DeviceNotConnected):
            continue
        mice.append(
            MouseDevice(
                device_path=device_path,
                device_index=index,
                name=name or "Мышь",
                protocol=protocol,
            )
        )
    return mice


def find_first_mouse(
    sys_hidraw: str = SYS_HIDRAW, dev_root: str = "/dev", *, timeout: float = 0.5
) -> MouseDevice | None:
    """Первая мышь в порядке обхода: ресиверы по номеру hidraw, внутри — по индексу."""
    for receiver in find_receivers(sys_hidraw, dev_root):
        try:
            with hidpp.Transport(receiver.device_path) as transport:
                mice = probe_mice(hidpp.Link(transport, timeout=timeout), receiver.device_path)
        except OSError:
            continue
        if mice:
            return mice[0]
    return None
