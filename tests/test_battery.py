from __future__ import annotations

import pytest

from battary import battery, hidpp

from .test_hidpp import link_with, short


def feature_reply(index: int) -> bytes:
    """Ответ root.getFeature с индексом фичи."""
    return short(1, 0x00, 0x0, index, 0x00, 0x00)


UNSUPPORTED = feature_reply(0x00)


class TestUnifiedBattery:
    def test_reads_percent_and_status(self) -> None:
        link, _ = link_with(
            feature_reply(0x06),
            short(1, 0x06, 0x1, 73, battery.LEVEL_GOOD, 0x00),
        )
        reading = battery.read_battery(link, 1)
        assert reading.percent == 73
        assert reading.status is battery.ChargeStatus.DISCHARGING
        assert reading.source == "0x1004"

    def test_charging_status(self) -> None:
        link, _ = link_with(
            feature_reply(0x06),
            short(1, 0x06, 0x1, 40, battery.LEVEL_GOOD, 0x01),
        )
        reading = battery.read_battery(link, 1)
        assert reading.is_charging

    def test_slow_charging_counts_as_charging(self) -> None:
        link, _ = link_with(
            feature_reply(0x06),
            short(1, 0x06, 0x1, 40, battery.LEVEL_GOOD, 0x02),
        )
        assert battery.read_battery(link, 1).is_charging

    def test_full(self) -> None:
        link, _ = link_with(
            feature_reply(0x06),
            short(1, 0x06, 0x1, 100, battery.LEVEL_FULL, 0x03),
        )
        reading = battery.read_battery(link, 1)
        assert reading.percent == 100
        assert reading.status is battery.ChargeStatus.FULL

    def test_falls_back_to_discrete_level_without_percent(self) -> None:
        """Некоторые устройства процент не отдают — используем уровень."""
        link, _ = link_with(
            feature_reply(0x06),
            short(1, 0x06, 0x1, 0, battery.LEVEL_LOW, 0x00),
        )
        assert battery.read_battery(link, 1).percent == 20

    def test_percent_is_none_when_nothing_is_known(self) -> None:
        link, _ = link_with(
            feature_reply(0x06),
            short(1, 0x06, 0x1, 0, 0x00, 0x00),
        )
        assert battery.read_battery(link, 1).percent is None

    def test_unknown_charging_code(self) -> None:
        link, _ = link_with(
            feature_reply(0x06),
            short(1, 0x06, 0x1, 50, battery.LEVEL_GOOD, 0x7F),
        )
        assert battery.read_battery(link, 1).status is battery.ChargeStatus.UNKNOWN


class TestLegacyFallback:
    def test_uses_0x1000_when_0x1004_is_absent(self) -> None:
        link, _ = link_with(
            UNSUPPORTED,  # нет 0x1004
            feature_reply(0x05),  # есть 0x1000
            short(1, 0x05, 0x0, 50, 20, 0x00),
        )
        reading = battery.read_battery(link, 1)
        assert reading.percent == 50
        assert reading.source == "0x1000"
        assert reading.status is battery.ChargeStatus.DISCHARGING

    def test_legacy_status_mapping(self) -> None:
        link, _ = link_with(
            UNSUPPORTED,
            feature_reply(0x05),
            short(1, 0x05, 0x0, 90, 50, 0x04),  # slow recharge
        )
        assert battery.read_battery(link, 1).is_charging

    def test_raises_when_no_battery_feature(self) -> None:
        link, _ = link_with(UNSUPPORTED, UNSUPPORTED)
        with pytest.raises(hidpp.HidppError):
            battery.read_battery(link, 1)


class TestShortReplies:
    def test_truncated_unified_reply_is_an_error(self) -> None:
        link, transport = link_with(feature_reply(0x06))
        transport.replies.append(bytes((0x10, 0x01, 0x06, 0x10 | hidpp.SOFTWARE_ID, 0x40)))
        with pytest.raises(hidpp.HidppError):
            battery.read_battery(link, 1)


class TestBatteryReader:
    def test_caches_feature_index_between_reads(self) -> None:
        link, transport = link_with(
            feature_reply(0x06),
            short(1, 0x06, 0x1, 73, battery.LEVEL_GOOD, 0x00),
            short(1, 0x06, 0x1, 72, battery.LEVEL_GOOD, 0x00),
        )
        reader = battery.BatteryReader(link, 1)
        assert reader.read().percent == 73
        assert reader.read().percent == 72
        # три запроса, а не четыре: getFeature выполнен единожды
        assert len(transport.written) == 3

    def test_forget_makes_next_read_resolve_feature_again(self) -> None:
        link, transport = link_with(
            feature_reply(0x06),
            short(1, 0x06, 0x1, 73, battery.LEVEL_GOOD, 0x00),
            feature_reply(0x06),
            short(1, 0x06, 0x1, 71, battery.LEVEL_GOOD, 0x00),
        )
        reader = battery.BatteryReader(link, 1)
        reader.read()
        reader.forget()
        assert reader.read().percent == 71
        assert len(transport.written) == 4
