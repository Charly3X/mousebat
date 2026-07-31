"""Чтение заряда: фича 0x1004 (UNIFIED_BATTERY), при её отсутствии — 0x1000.

Ничего не знает про Qt и про то, как искалось устройство.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import hidpp

FUNC_UNIFIED_GET_CAPABILITIES = 0x0
FUNC_UNIFIED_GET_STATUS = 0x1
FUNC_LEGACY_GET_STATUS = 0x0

#: Флаги дискретного уровня в 0x1004 и их разумное представление в процентах.
LEVEL_CRITICAL = 1 << 0
LEVEL_LOW = 1 << 1
LEVEL_GOOD = 1 << 2
LEVEL_FULL = 1 << 3
LEVEL_PERCENT = ((LEVEL_FULL, 95), (LEVEL_GOOD, 60), (LEVEL_LOW, 20), (LEVEL_CRITICAL, 5))


class ChargeStatus(Enum):
    DISCHARGING = "разряжается"
    CHARGING = "заряжается"
    FULL = "заряжена"
    ERROR = "ошибка зарядки"
    UNKNOWN = "состояние неизвестно"


#: 0x1004 getStatus, params[2]
UNIFIED_STATUS = {
    0: ChargeStatus.DISCHARGING,
    1: ChargeStatus.CHARGING,
    2: ChargeStatus.CHARGING,  # медленная зарядка
    3: ChargeStatus.FULL,
    4: ChargeStatus.ERROR,
}

#: 0x1000 getBatteryLevelStatus, params[2]
LEGACY_STATUS = {
    0: ChargeStatus.DISCHARGING,
    1: ChargeStatus.CHARGING,
    2: ChargeStatus.CHARGING,  # almost full
    3: ChargeStatus.FULL,
    4: ChargeStatus.CHARGING,  # slow recharge
    5: ChargeStatus.ERROR,
    6: ChargeStatus.ERROR,
}


@dataclass(frozen=True)
class BatteryReading:
    percent: int | None
    status: ChargeStatus
    source: str  # "0x1004" или "0x1000" — какой фичей получено

    @property
    def is_charging(self) -> bool:
        return self.status is ChargeStatus.CHARGING


def _percent_from_level(flags: int) -> int | None:
    for flag, percent in LEVEL_PERCENT:
        if flags & flag:
            return percent
    return None


def _read_unified(link: hidpp.Link, device_index: int, feature: int) -> BatteryReading:
    params = link.request(device_index, feature, FUNC_UNIFIED_GET_STATUS)
    if len(params) < 3:
        raise hidpp.HidppError(0x01, protocol=2, message="short battery reply")
    charge, level_flags, charging = params[0], params[1], params[2]
    percent = charge if 0 < charge <= 100 else _percent_from_level(level_flags)
    return BatteryReading(
        percent=percent,
        status=UNIFIED_STATUS.get(charging, ChargeStatus.UNKNOWN),
        source="0x1004",
    )


def _read_legacy(link: hidpp.Link, device_index: int, feature: int) -> BatteryReading:
    params = link.request(device_index, feature, FUNC_LEGACY_GET_STATUS)
    if len(params) < 3:
        raise hidpp.HidppError(0x01, protocol=2, message="short battery reply")
    percent = params[0] if 0 < params[0] <= 100 else None
    return BatteryReading(
        percent=percent,
        status=LEGACY_STATUS.get(params[2], ChargeStatus.UNKNOWN),
        source="0x1000",
    )


def read_battery(link: hidpp.Link, device_index: int) -> BatteryReading:
    """Прочитать заряд устройства. Бросает исключения hidpp при недоступности."""
    unified = link.feature_index(device_index, hidpp.FEATURE_UNIFIED_BATTERY)
    if unified is not None:
        return _read_unified(link, device_index, unified)

    legacy = link.feature_index(device_index, hidpp.FEATURE_BATTERY_STATUS)
    if legacy is not None:
        return _read_legacy(link, device_index, legacy)

    raise hidpp.HidppError(0x09, protocol=2, message="no battery feature")


class BatteryReader:
    """Многократное чтение одного устройства с кэшем индекса фичи.

    Индекс фичи у устройства не меняется, пока связь жива, поэтому храним его и
    экономим один запрос на каждый опрос. Кэш сбрасывается через `forget()` —
    трей вызывает его при потере связи.
    """

    def __init__(self, link: hidpp.Link, device_index: int) -> None:
        self.link = link
        self.device_index = device_index
        self._feature: tuple[int, int] | None = None  # (feature_id, index)

    def forget(self) -> None:
        self._feature = None

    def _resolve_feature(self) -> tuple[int, int]:
        if self._feature is not None:
            return self._feature
        for feature_id in (hidpp.FEATURE_UNIFIED_BATTERY, hidpp.FEATURE_BATTERY_STATUS):
            index = self.link.feature_index(self.device_index, feature_id)
            if index is not None:
                self._feature = (feature_id, index)
                return self._feature
        raise hidpp.HidppError(0x09, protocol=2, message="no battery feature")

    def read(self) -> BatteryReading:
        feature_id, index = self._resolve_feature()
        if feature_id == hidpp.FEATURE_UNIFIED_BATTERY:
            return _read_unified(self.link, self.device_index, index)
        return _read_legacy(self.link, self.device_index, index)
