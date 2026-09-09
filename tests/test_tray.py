"""Tray state transitions, driven by stubbed samples instead of real polling."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="requires python3-pyqt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from mousebat import battery, discovery, icon, tray  # noqa: E402
from mousebat.autostart import Autostart  # noqa: E402

from .test_autostart import FakeSystemctl  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


KEYBOARD = discovery.HidppDevice(
    "/dev/hidraw4", 6, "MX Keys", (4, 5), discovery.DEVICE_TYPE_KEYBOARD
)
MOUSE = discovery.HidppDevice(
    "/dev/hidraw3", 2, "MX Master 3S", (4, 5), discovery.DEVICE_TYPE_MOUSE
)


def make_item(device: discovery.HidppDevice, fake: FakeSystemctl | None = None) -> tray.DeviceItem:
    runner = fake if fake is not None else FakeSystemctl()
    return tray.DeviceItem(QApplication.instance(), device, Autostart(runner=runner))


def reading(percent: int | None, status: battery.ChargeStatus) -> battery.BatteryReading:
    return battery.BatteryReading(percent=percent, status=status, source="0x1000")


class TestShapeForType:
    def test_keyboard(self) -> None:
        assert tray.shape_for(discovery.DEVICE_TYPE_KEYBOARD) is icon.Shape.KEYBOARD

    def test_mouse_and_trackball_share_a_shape(self) -> None:
        assert tray.shape_for(discovery.DEVICE_TYPE_MOUSE) is icon.Shape.MOUSE
        assert tray.shape_for(discovery.DEVICE_TYPE_TRACKBALL) is icon.Shape.MOUSE

    def test_unknown_type_falls_back_to_a_battery(self) -> None:
        assert tray.shape_for(0x42) is icon.Shape.BATTERY


class TestDeviceItem:
    def test_titled_after_its_device_at_creation(self) -> None:
        """Qt freezes the title when the item is created, so the name must be known."""
        item = make_item(KEYBOARD)
        assert item._icon is not None
        assert QApplication.instance().applicationDisplayName() == "MX Keys"

    def test_online_tooltip_shows_name_and_percent(self) -> None:
        item = make_item(KEYBOARD)
        item._apply(tray.Sample(name="MX Keys", reading=reading(100, battery.ChargeStatus.FULL)))
        assert item._icon.toolTip() == "MX Keys\n100% — fully charged"

    def test_offline_tooltip_keeps_the_device_name(self) -> None:
        item = make_item(MOUSE)
        item._apply(tray.Sample(name=None, reading=None, detail="timeout"))
        assert item._icon.toolTip().startswith("MX Master 3S\nno connection")

    def test_unknown_percent_is_shown_as_a_dash(self) -> None:
        item = make_item(MOUSE)
        item._apply(
            tray.Sample(name="MX Master 3S", reading=reading(None, battery.ChargeStatus.DISCHARGING))
        )
        assert "—" in item._icon.toolTip()

    def test_offline_polls_more_often(self) -> None:
        item = make_item(MOUSE)
        item._apply(tray.Sample(name=None, reading=None, detail="asleep"))
        assert item._timer.interval() == tray.OFFLINE_INTERVAL_MS

    def test_back_online_restores_the_slow_interval(self) -> None:
        item = make_item(MOUSE)
        item._apply(tray.Sample(name=None, reading=None))
        item._apply(
            tray.Sample(name="MX Master 3S", reading=reading(50, battery.ChargeStatus.CHARGING))
        )
        assert item._timer.interval() == tray.POLL_INTERVAL_MS

    def test_the_item_is_never_replaced(self) -> None:
        """Re-creating a tray item drops it from the panel for good."""
        item = make_item(MOUSE)
        first = item._icon
        item._apply(tray.Sample(name=None, reading=None))
        item._apply(
            tray.Sample(name="MX Master 3S", reading=reading(50, battery.ChargeStatus.CHARGING))
        )
        assert item._icon is first


class FakeReader:
    """Answers one reading, or raises to mimic a device that has gone quiet."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.reads = 0

    def read(self) -> battery.BatteryReading:
        self.reads += 1
        if self.fail:
            raise tray.hidpp.HidppTimeout("asleep")
        return reading(100, battery.ChargeStatus.DISCHARGING)

    def forget(self) -> None:
        pass


class TestPollerConnect:
    """The known path is tried before the receivers are walked again.

    A walk takes seconds, wakes every device and locks out the other pollers, so it
    has to be the fallback. Getting this wrong was a real bug: the first poll of every
    device scanned, two pollers scanned at once, and their replies crossed.
    """

    def stub(self, monkeypatch, reader: FakeReader) -> list[str]:
        """Record relocation attempts and hand out a fake link."""
        relocations: list[str] = []

        def relocate(device):  # noqa: ANN001, ANN202
            relocations.append(device.name)
            return device

        class Transport:
            def close(self) -> None:
                pass

        monkeypatch.setattr(tray.discovery, "relocate", relocate)
        monkeypatch.setattr(tray.hidpp, "Transport", lambda path: Transport())
        monkeypatch.setattr(tray.hidpp, "Link", lambda transport: object())
        monkeypatch.setattr(tray.battery, "BatteryReader", lambda link, index: reader)
        return relocations

    def test_first_poll_does_not_scan(self, monkeypatch) -> None:
        reader = FakeReader()
        relocations = self.stub(monkeypatch, reader)
        poller = tray.Poller(KEYBOARD)

        sample = poller._sample()
        assert sample.online
        assert relocations == []
        assert reader.reads == 1

    def test_a_failed_read_forces_a_scan_next_time(self, monkeypatch) -> None:
        reader = FakeReader(fail=True)
        relocations = self.stub(monkeypatch, reader)
        poller = tray.Poller(KEYBOARD)

        first = poller._sample()
        assert not first.online
        assert relocations == []  # the failure itself does not scan

        poller._sample()
        assert relocations == ["MX Keys"]  # the next attempt does

    def test_a_missing_device_is_reported_not_raised(self, monkeypatch) -> None:
        monkeypatch.setattr(tray.discovery, "relocate", lambda device: None)
        monkeypatch.setattr(
            tray.hidpp, "Transport", lambda path: (_ for _ in ()).throw(OSError("gone"))
        )
        poller = tray.Poller(KEYBOARD)
        sample = poller._sample()
        assert not sample.online
        assert sample.detail == "device not found"


class TestItemMenu:
    def test_refresh_can_be_omitted(self) -> None:
        with_refresh = tray.ItemMenu(
            QApplication.instance(), Autostart(runner=FakeSystemctl()), with_refresh=True
        )
        without = tray.ItemMenu(
            QApplication.instance(), Autostart(runner=FakeSystemctl()), with_refresh=False
        )
        assert "Refresh" in [action.text() for action in with_refresh.menu.actions()]
        assert "Refresh" not in [action.text() for action in without.menu.actions()]

    def test_checkbox_mirrors_the_unit(self) -> None:
        menu = tray.ItemMenu(
            QApplication.instance(),
            Autostart(runner=FakeSystemctl(enabled=True)),
            with_refresh=True,
        )
        menu.sync()
        assert menu._autostart_action.isChecked()

    def test_hidden_without_an_installed_unit(self) -> None:
        menu = tray.ItemMenu(
            QApplication.instance(),
            Autostart(runner=FakeSystemctl(installed=False)),
            with_refresh=True,
        )
        menu.sync()
        assert not menu._autostart_action.isVisible()

    def test_toggling_calls_systemctl(self) -> None:
        fake = FakeSystemctl(enabled=False)
        menu = tray.ItemMenu(QApplication.instance(), Autostart(runner=fake), with_refresh=True)
        menu._autostart_action.setChecked(True)
        assert "enable" in fake.verbs
        assert fake.state is True

    def test_syncing_does_not_call_enable_or_disable(self) -> None:
        """Refreshing the checkmark must not flip the unit as a side effect."""
        fake = FakeSystemctl(enabled=True)
        menu = tray.ItemMenu(QApplication.instance(), Autostart(runner=fake), with_refresh=True)
        fake.calls.clear()
        menu.sync()
        assert "enable" not in fake.verbs
        assert "disable" not in fake.verbs

    def test_refusal_reverts_the_checkmark(self) -> None:
        fake = FakeSystemctl(enabled=False)
        fake.refuse = True
        menu = tray.ItemMenu(QApplication.instance(), Autostart(runner=fake), with_refresh=True)
        menu._autostart_action.setChecked(True)
        assert not menu._autostart_action.isChecked()


def make_tray(fake: FakeSystemctl | None = None) -> tray.Tray:
    runner = fake if fake is not None else FakeSystemctl()
    return tray.Tray(QApplication.instance(), autostart=Autostart(runner=runner))


@pytest.fixture
def no_polling(monkeypatch) -> None:
    """Keep `Tray` tests off the real hardware.

    `Tray._build_items` calls `DeviceItem.start()`, which starts a worker thread that
    would go looking for actual receivers under `/dev`. Creating the items is what is
    under test here; polling them is `DeviceItem`'s own business.
    """
    monkeypatch.setattr(tray.DeviceItem, "start", lambda self: None)


class TestTray:
    def test_one_item_per_device(self, no_polling) -> None:
        widget = make_tray()
        widget._build_items([KEYBOARD, MOUSE])
        assert len(widget._items) == 2
        assert widget._placeholder is None
        widget._stop()

    def test_items_follow_the_discovery_order(self, no_polling) -> None:
        widget = make_tray()
        widget._build_items([KEYBOARD, MOUSE])
        assert [item._device.name for item in widget._items] == ["MX Keys", "MX Master 3S"]
        widget._stop()

    def test_placeholder_when_nothing_is_found(self, no_polling) -> None:
        """The menu lives on a tray icon, so with no items there is no way to quit."""
        widget = make_tray()
        widget._build_items([])
        assert widget._items == []
        assert widget._placeholder is not None
        widget._stop()

    def test_application_name_is_restored_after_building(self, no_polling) -> None:
        """Each item steals the application name to get its title; put it back."""
        widget = make_tray()
        widget._build_items([KEYBOARD, MOUSE])
        assert QApplication.instance().applicationDisplayName() == tray.FALLBACK_TITLE
        widget._stop()

    def test_no_items_before_discovery_finishes(self) -> None:
        widget = make_tray()
        assert widget._items == []
        assert widget._placeholder is None
