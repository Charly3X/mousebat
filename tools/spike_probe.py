#!/usr/bin/env python3
"""Диагностика: что реально отвечает по HID++ на этой машине.

Печатает найденные hidraw-узлы ресиверов, результат ping по индексам 1..6,
тип и имя устройства, индексы батарейных фич и сырые байты ответа о заряде.

Запуск из корня проекта:  python3 tools/spike_probe.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from battary import battery, discovery, hidpp  # noqa: E402


def hexs(data: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in data)


def probe_index(link: hidpp.Link, index: int) -> None:
    try:
        major, minor = link.ping(index)
    except Exception as exc:  # noqa: BLE001 — диагностика, печатаем любую причину
        print(f"    index {index}: ping — {type(exc).__name__}: {exc}")
        return

    print(f"    index {index}: HID++ {major}.{minor}")

    try:
        name_feature = link.feature_index(index, hidpp.FEATURE_DEVICE_NAME)
        if name_feature is None:
            print("      0x0005 DEVICE_NAME не поддерживается")
        else:
            kind = discovery.device_type(link, index, name_feature)
            name = discovery.device_name(link, index, name_feature)
            is_pointer = kind in discovery.POINTER_TYPES
            print(
                f"      имя: {name!r}, тип: 0x{kind:02X}"
                f" ({'указывающее устройство' if is_pointer else 'другое'})"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"      имя/тип — {type(exc).__name__}: {exc}")

    for feature_id, label in (
        (hidpp.FEATURE_UNIFIED_BATTERY, "0x1004 UNIFIED_BATTERY"),
        (hidpp.FEATURE_BATTERY_STATUS, "0x1000 BATTERY_STATUS"),
    ):
        try:
            found = link.feature_index(index, feature_id)
        except Exception as exc:  # noqa: BLE001
            print(f"      {label}: запрос индекса — {type(exc).__name__}: {exc}")
            continue
        if found is None:
            print(f"      {label}: не поддерживается")
            continue
        print(f"      {label}: индекс {found}")
        function = (
            battery.FUNC_UNIFIED_GET_STATUS
            if feature_id == hidpp.FEATURE_UNIFIED_BATTERY
            else battery.FUNC_LEGACY_GET_STATUS
        )
        try:
            params = link.request(index, found, function)
            print(f"        сырые параметры: {hexs(params)}")
        except Exception as exc:  # noqa: BLE001
            print(f"        чтение — {type(exc).__name__}: {exc}")

    try:
        reading = battery.read_battery(link, index)
        print(f"      ИТОГ: {reading.percent}% — {reading.status.value} (через {reading.source})")
    except Exception as exc:  # noqa: BLE001
        print(f"      ИТОГ: не прочитано — {type(exc).__name__}: {exc}")


def main() -> int:
    receivers = discovery.find_receivers()
    if not receivers:
        print("HID++-узлов Logitech не найдено.")
        print("Проверь, что ресивер подключён, и что udev-правило установлено.")
        return 1

    print(f"Найдено HID++-узлов: {len(receivers)}")
    failures = 0
    for receiver in receivers:
        print(f"\n  {receiver.device_path}  (PID 0x{receiver.product_id:04X})")
        try:
            transport = hidpp.Transport(receiver.device_path)
        except OSError as exc:
            print(f"    открыть не удалось: {exc}")
            print("    → нужно udev-правило, см. packaging/42-battary-hidraw.rules")
            failures += 1
            continue
        with transport:
            link = hidpp.Link(transport)
            for index in discovery.DEVICE_INDICES:
                probe_index(link, index)

    print("\n--- как это увидит апплет ---")
    mouse = discovery.find_first_mouse()
    if mouse is None:
        print("Мышь не найдена.")
        return 1 if failures else 0
    print(f"Мышь: {mouse.name!r} на {mouse.device_path}, индекс {mouse.device_index}")
    with hidpp.Transport(mouse.device_path) as transport:
        reading = battery.read_battery(hidpp.Link(transport), mouse.device_index)
    print(f"Заряд: {reading.percent}% — {reading.status.value} (через {reading.source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
