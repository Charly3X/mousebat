"""Tray state transitions, driven by stubbed samples instead of real polling."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="requires python3-pyqt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mousebat import battery, tray  # noqa: E402

from .test_autostart import FakeSystemctl  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def make_tray(fake: FakeSystemctl) -> tray.Tray:
    from mousebat.autostart import Autostart

    return tray.Tray(QApplication.instance(), autostart=Autostart(runner=fake))


def reading(percent: int | None, status: battery.ChargeStatus) -> battery.BatteryReading:
    return battery.BatteryReading(percent=percent, status=status, source="0x1004")


class TestToolTip:
    def test_online_shows_name_and_percent(self) -> None:
        widget = make_tray(FakeSystemctl())
        widget._apply(
            tray.Sample(name="MX Master 3S", reading=reading(73, battery.ChargeStatus.DISCHARGING))
        )
        assert widget._icon.toolTip() == "MX Master 3S\n73% — discharging"

    def test_offline_keeps_the_last_known_name(self) -> None:
        widget = make_tray(FakeSystemctl())
        widget._apply(
            tray.Sample(name="MX Master 3S", reading=reading(73, battery.ChargeStatus.DISCHARGING))
        )
        widget._apply(tray.Sample(name=None, reading=None, detail="timeout"))
        assert widget._icon.toolTip().startswith("MX Master 3S\nno connection")

    def test_unknown_percent_is_shown_as_a_dash(self) -> None:
        widget = make_tray(FakeSystemctl())
        widget._apply(
            tray.Sample(name="Mouse", reading=reading(None, battery.ChargeStatus.DISCHARGING))
        )
        assert "—" in widget._icon.toolTip()


class TestPollInterval:
    def test_offline_polls_more_often(self) -> None:
        widget = make_tray(FakeSystemctl())
        widget._apply(tray.Sample(name=None, reading=None, detail="no mouse found"))
        assert widget._timer.interval() == tray.OFFLINE_INTERVAL_MS

    def test_back_online_restores_the_slow_interval(self) -> None:
        widget = make_tray(FakeSystemctl())
        widget._apply(tray.Sample(name=None, reading=None))
        widget._apply(
            tray.Sample(name="MX Master 3S", reading=reading(50, battery.ChargeStatus.CHARGING))
        )
        assert widget._timer.interval() == tray.POLL_INTERVAL_MS


class TestAutostartAction:
    def test_checkbox_mirrors_the_unit(self) -> None:
        widget = make_tray(FakeSystemctl(enabled=True))
        widget._sync_autostart_action()
        assert widget._autostart_action.isChecked()

    def test_hidden_without_an_installed_unit(self) -> None:
        widget = make_tray(FakeSystemctl(installed=False))
        widget._sync_autostart_action()
        assert not widget._autostart_action.isVisible()

    def test_toggling_calls_systemctl(self) -> None:
        fake = FakeSystemctl(enabled=False)
        widget = make_tray(fake)
        widget._autostart_action.setChecked(True)
        assert "enable" in fake.verbs
        assert fake.state is True

    def test_syncing_does_not_call_enable_or_disable(self) -> None:
        """Refreshing the checkmark must not flip the unit as a side effect."""
        fake = FakeSystemctl(enabled=True)
        widget = make_tray(fake)
        fake.calls.clear()
        widget._sync_autostart_action()
        assert "enable" not in fake.verbs
        assert "disable" not in fake.verbs

    def test_refusal_reverts_the_checkmark(self) -> None:
        fake = FakeSystemctl(enabled=False)
        fake.refuse = True
        widget = make_tray(fake)
        widget._autostart_action.setChecked(True)
        assert not widget._autostart_action.isChecked()
