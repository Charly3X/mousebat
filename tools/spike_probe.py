#!/usr/bin/env python3
"""Diagnostics: what actually answers HID++ on this machine.

Prints the hidraw nodes found, the ping result for indices 1..6, each device's
type and name, the battery feature indices and the raw bytes of the charge reply.

Run from the project root:  python3 tools/spike_probe.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mousebat import battery, discovery, hidpp  # noqa: E402


#: Device type codes of feature 0x0005, for readable output.
TYPE_NAMES = {
    discovery.DEVICE_TYPE_KEYBOARD: "keyboard",
    discovery.DEVICE_TYPE_MOUSE: "mouse",
    discovery.DEVICE_TYPE_TRACKBALL: "trackball",
    0x07: "receiver",
}


def hexs(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def probe_index(link: hidpp.Link, index: int) -> None:
    try:
        major, minor = link.ping(index)
    except Exception as exc:  # noqa: BLE001 — diagnostics: report any cause
        print(f"    index {index}: ping — {type(exc).__name__}: {exc}")
        return

    print(f"    index {index}: HID++ {major}.{minor}")

    try:
        name_feature = link.feature_index(index, hidpp.FEATURE_DEVICE_NAME)
        if name_feature is None:
            print("      0x0005 DEVICE_NAME unsupported")
        else:
            kind = discovery.device_type(link, index, name_feature)
            name = discovery.device_name(link, index, name_feature)
            print(
                f"      name: {name!r}, type: 0x{kind:02X}"
                f" ({TYPE_NAMES.get(kind, 'unrecognised')},"
                f" {'battery expected' if kind in discovery.BATTERY_DEVICE_TYPES else 'skipped'})"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"      name/type — {type(exc).__name__}: {exc}")

    for feature_id, label in (
        (hidpp.FEATURE_UNIFIED_BATTERY, "0x1004 UNIFIED_BATTERY"),
        (hidpp.FEATURE_BATTERY_STATUS, "0x1000 BATTERY_STATUS"),
    ):
        try:
            found = link.feature_index(index, feature_id)
        except Exception as exc:  # noqa: BLE001
            print(f"      {label}: index lookup — {type(exc).__name__}: {exc}")
            continue
        if found is None:
            print(f"      {label}: unsupported")
            continue
        print(f"      {label}: index {found}")
        function = (
            battery.FUNC_UNIFIED_GET_STATUS
            if feature_id == hidpp.FEATURE_UNIFIED_BATTERY
            else battery.FUNC_LEGACY_GET_STATUS
        )
        try:
            params = link.request(index, found, function)
            print(f"        raw params: {hexs(params)}")
        except Exception as exc:  # noqa: BLE001
            print(f"        read — {type(exc).__name__}: {exc}")

    try:
        reading = battery.read_battery(link, index)
        print(f"      RESULT: {reading.percent}% — {reading.status.value} (via {reading.source})")
    except Exception as exc:  # noqa: BLE001
        print(f"      RESULT: unreadable — {type(exc).__name__}: {exc}")


def main() -> int:
    receivers = discovery.find_receivers()
    if not receivers:
        print("No Logitech HID++ nodes found.")
        print("Check that the receiver is plugged in and the udev rule is installed.")
        return 1

    print(f"HID++ nodes found: {len(receivers)}")
    failures = 0
    for receiver in receivers:
        print(f"\n  {receiver.device_path}  (PID 0x{receiver.product_id:04X})")
        try:
            transport = hidpp.Transport(receiver.device_path)
        except OSError as exc:
            print(f"    cannot open: {exc}")
            print("    -> the udev rule is needed, see packaging/42-mousebat-hidraw.rules")
            failures += 1
            continue
        with transport:
            link = hidpp.Link(transport)
            for index in discovery.DEVICE_INDICES:
                probe_index(link, index)

    print("\n--- what the applet will see ---")
    devices = discovery.find_devices()
    if not devices:
        print("No device found.")
        return 1 if failures else 0
    for device in devices:
        kind = TYPE_NAMES.get(device.kind, "unrecognised")
        print(
            f"{device.name!r} ({kind}) on {device.device_path}, index {device.device_index}"
        )
        try:
            with hidpp.Transport(device.device_path) as transport:
                link = hidpp.Link(transport)
                # A device that dozed off during the walk above answers the retrying
                # ping but not a single-shot feature lookup, so wake it first.
                link.ping(device.device_index)
                reading = battery.read_battery(link, device.device_index)
        except Exception as exc:  # noqa: BLE001 — diagnostics: report any cause
            print(f"  charge: unreadable — {type(exc).__name__}: {exc}")
            failures += 1
            continue
        print(f"  charge: {reading.percent}% — {reading.status.value} (via {reading.source})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
