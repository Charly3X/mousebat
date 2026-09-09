"""Tray items, one per device, each polling on its own worker thread.

Polling blocks: a lost link means re-walking receivers and indices, which takes
seconds, so it lives on a worker thread while the GUI only receives finished
results. One thread per device keeps a sleeping device from delaying its neighbour.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import battery, discovery, hidpp, icon
from .autostart import Autostart
from .icon import make_icon

#: Charge drifts slowly, and every extra request wakes the device up.
POLL_INTERVAL_MS = 5 * 60 * 1000
#: Link lost — check more often so the device is picked up quickly once it returns.
OFFLINE_INTERVAL_MS = 60 * 1000

FALLBACK_TITLE = "mousebat"

#: A trackball is a mouse as far as the panel is concerned.
SHAPE_FOR_TYPE = {
    discovery.DEVICE_TYPE_KEYBOARD: icon.Shape.KEYBOARD,
    discovery.DEVICE_TYPE_MOUSE: icon.Shape.MOUSE,
    discovery.DEVICE_TYPE_TRACKBALL: icon.Shape.MOUSE,
}


def shape_for(kind: int) -> icon.Shape:
    """The silhouette for a HID++ device type; a plain battery for anything unknown."""
    return SHAPE_FOR_TYPE.get(kind, icon.Shape.BATTERY)


@dataclass(frozen=True)
class Sample:
    """The outcome of a single poll."""

    name: str | None
    reading: battery.BatteryReading | None
    detail: str = ""

    @property
    def online(self) -> bool:
        return self.reading is not None


class Poller(QObject):
    """Lives on the worker thread, polling one known device."""

    sampled = pyqtSignal(object)  # Sample

    def __init__(self, device: discovery.HidppDevice) -> None:
        super().__init__()
        self._device = device
        self._transport: hidpp.Transport | None = None
        self._reader: battery.BatteryReader | None = None
        #: Discovery just handed us this path, so it is worth trying before scanning.
        #: Cleared by a failed read, which is the sign the node may have moved.
        self._trust_path = True

    @pyqtSlot()
    def poll(self) -> None:
        try:
            self.sampled.emit(self._sample())
        except Exception as exc:  # noqa: BLE001 — a poll must never kill the thread
            self._drop()
            self.sampled.emit(Sample(name=self._device.name, reading=None, detail=str(exc)))

    def _sample(self) -> Sample:
        if self._reader is None and not self._connect():
            return Sample(name=self._device.name, reading=None, detail="device not found")

        assert self._reader is not None
        try:
            reading = self._reader.read()
        except (hidpp.HidppTimeout, hidpp.DeviceNotConnected, hidpp.HidppError, OSError) as exc:
            # Asleep, or the receiver moved to another node: start over next time,
            # and stop trusting the path until a read proves it again.
            self._drop()
            self._trust_path = False
            return Sample(name=self._device.name, reading=None, detail=str(exc))
        self._trust_path = True
        return Sample(name=self._device.name, reading=reading)

    def _connect(self) -> bool:
        """Open a link to the device, scanning for it only when the path fails.

        The path comes from discovery and is normally still right. Walking the
        receivers to find the device again costs seconds, wakes every device and has
        to take a lock against the other pollers, so it is the fallback rather than
        the first move. Only a failed read marks the path stale: the node itself goes
        on opening fine after the device behind it has gone quiet.
        """
        if self._trust_path and self._open(self._device):
            return True

        # hidraw numbering changes when a receiver is replugged, so the device is
        # found again by identity rather than by its old path.
        relocated = discovery.relocate(self._device)
        if relocated is None:
            return False
        return self._open(relocated)

    def _open(self, device: discovery.HidppDevice) -> bool:
        try:
            transport = hidpp.Transport(device.device_path)
        except OSError:
            return False
        self._device = device
        self._transport = transport
        self._reader = battery.BatteryReader(hidpp.Link(transport), device.device_index)
        return True

    def _drop(self) -> None:
        if self._reader is not None:
            self._reader.forget()
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._reader = None

    @pyqtSlot()
    def shutdown(self) -> None:
        self._drop()


class ItemMenu(QObject):
    """The context menu of one tray item.

    `Refresh` belongs to the item that owns the menu and is omitted when there is
    nothing to refresh. `Start at login` and `Quit` are process-wide: `Autostart`
    governs a single unit, so every item's checkbox reflects the same state.
    """

    refresh_requested = pyqtSignal()

    def __init__(self, app: QApplication, autostart: Autostart, *, with_refresh: bool) -> None:
        super().__init__()
        self._autostart = autostart

        menu = QMenu()
        if with_refresh:
            menu.addAction("Refresh").triggered.connect(self.refresh_requested.emit)

        self._autostart_action = menu.addAction("Start at login")
        self._autostart_action.setCheckable(True)
        self._autostart_action.toggled.connect(self._set_autostart)
        # Autostart may have been changed from a terminal meanwhile, so the checkmark
        # is refreshed every time the menu opens rather than once.
        menu.aboutToShow.connect(self.sync)
        self.sync()

        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(app.quit)
        self.menu = menu

    def sync(self) -> None:
        """Mirror the unit's real state into the checkbox.

        Hidden when no unit is installed — the applet was started by hand, and there
        is nothing to enable.
        """
        available = self._autostart.available()
        self._autostart_action.setVisible(available)
        if not available:
            return
        # Assigning setChecked would re-emit toggled and call systemctl again.
        self._autostart_action.blockSignals(True)
        self._autostart_action.setChecked(self._autostart.enabled())
        self._autostart_action.blockSignals(False)

    @pyqtSlot(bool)
    def _set_autostart(self, value: bool) -> None:
        reached = self._autostart.set_enabled(value)
        if reached != value:
            # systemctl refused; show what is actually true.
            self._autostart_action.blockSignals(True)
            self._autostart_action.setChecked(reached)
            self._autostart_action.blockSignals(False)


class DeviceItem(QObject):
    """One device: its tray icon, menu, worker thread and poll timer."""

    poll_requested = pyqtSignal()

    def __init__(
        self, app: QApplication, device: discovery.HidppDevice, autostart: Autostart
    ) -> None:
        super().__init__()
        self._app = app
        self._device = device
        self._shape = shape_for(device.kind)

        self._item_menu = ItemMenu(app, autostart, with_refresh=True)
        self._item_menu.refresh_requested.connect(self.poll_requested.emit)
        self._icon = self._create_icon(device.name)

        self._thread = QThread()
        self._poller = Poller(device)
        self._poller.moveToThread(self._thread)
        self._poller.sampled.connect(self._apply)
        self.poll_requested.connect(self._poller.poll)  # queued: runs on the worker

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll_requested.emit)

    def _create_icon(self, title: str) -> QSystemTrayIcon:
        """Create the tray item, titled after the device.

        Plasma shows this title in the collapsed-items list, and Qt copies it from the
        application name at creation — there is no way to change it afterwards, and
        re-creating the item makes it vanish from the tray for good. Discovery has
        already named the device, so the name is set first and the icon second.
        """
        self._app.setApplicationName(title)
        self._app.setApplicationDisplayName(title)

        item = QSystemTrayIcon()
        item.setIcon(make_icon(None, shape=self._shape, offline=True))
        item.setToolTip(f"{title} — polling…")
        item.setContextMenu(self._item_menu.menu)
        item.show()
        return item

    def start(self) -> None:
        self._thread.start()
        self._timer.start()
        self.poll_requested.emit()

    @pyqtSlot(object)
    def _apply(self, sample: Sample) -> None:
        name = sample.name or self._device.name

        if sample.online:
            reading = sample.reading
            assert reading is not None
            percent_text = "—" if reading.percent is None else f"{reading.percent}%"
            self._icon.setIcon(
                make_icon(reading.percent, shape=self._shape, charging=reading.is_charging)
            )
            self._icon.setToolTip(f"{name}\n{percent_text} — {reading.status.value}")
            self._timer.setInterval(POLL_INTERVAL_MS)
        else:
            self._icon.setIcon(make_icon(None, shape=self._shape, offline=True))
            detail = f"\n{sample.detail}" if sample.detail else ""
            self._icon.setToolTip(f"{name}\nno connection{detail}")
            self._timer.setInterval(OFFLINE_INTERVAL_MS)

    def stop(self) -> None:
        self._timer.stop()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        # The thread has stopped, so closing the transport here is safe.
        self._poller.shutdown()


class PlaceholderItem:
    """A tray item shown when no device was found.

    Without it the applet would have no icon at all, and the context menu — the only
    way to quit or to toggle autostart — lives on the icon. It polls nothing: picking
    up a device that appears later takes a restart.
    """

    def __init__(self, app: QApplication, autostart: Autostart) -> None:
        app.setApplicationName(FALLBACK_TITLE)
        app.setApplicationDisplayName(FALLBACK_TITLE)

        self._item_menu = ItemMenu(app, autostart, with_refresh=False)
        self._icon = QSystemTrayIcon()
        self._icon.setIcon(make_icon(None, offline=True))
        self._icon.setToolTip(f"{FALLBACK_TITLE}\nno device found")
        self._icon.setContextMenu(self._item_menu.menu)
        self._icon.show()


class Scanner(QObject):
    """Runs the one blocking discovery pass, off the GUI thread."""

    found = pyqtSignal(object)  # list[discovery.HidppDevice]

    @pyqtSlot()
    def scan(self) -> None:
        try:
            devices = discovery.find_devices()
        except Exception:  # noqa: BLE001 — an empty result still yields a usable tray
            devices = []
        self.found.emit(devices)


class Tray(QObject):
    """Discovers devices once at startup, then owns one item per device.

    The device set is fixed from then on: items are only ever added, never removed,
    because Plasma drops a re-created tray item permanently.
    """

    def __init__(self, app: QApplication, autostart: Autostart | None = None) -> None:
        super().__init__()
        self._app = app
        self._autostart = autostart if autostart is not None else Autostart()
        self._items: list[DeviceItem] = []
        self._placeholder: PlaceholderItem | None = None
        self._scan_thread: QThread | None = None
        self._scanner: Scanner | None = None
        app.aboutToQuit.connect(self._stop)

    def start(self) -> None:
        thread = QThread()
        scanner = Scanner()
        scanner.moveToThread(thread)
        scanner.found.connect(self._build_items)
        thread.started.connect(scanner.scan)
        # Both are kept for the lifetime of the tray: a garbage-collected QThread
        # would take the scan down with it.
        self._scan_thread = thread
        self._scanner = scanner
        thread.start()

    @pyqtSlot(object)
    def _build_items(self, devices: list[discovery.HidppDevice]) -> None:
        for device in devices:
            item = DeviceItem(self._app, device, self._autostart)
            item.start()
            self._items.append(item)

        if not self._items:
            self._placeholder = PlaceholderItem(self._app, self._autostart)

        # Every item took the application name to title itself; restore ours.
        self._app.setApplicationName(FALLBACK_TITLE)
        self._app.setApplicationDisplayName(FALLBACK_TITLE)

        if self._scan_thread is not None:
            self._scan_thread.quit()

    def _stop(self) -> None:
        for item in self._items:
            item.stop()
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(3000)
