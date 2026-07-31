"""The tray icon, plus device polling on a worker thread.

Polling blocks: with the link lost, walking receivers and indices takes seconds,
so it lives on a worker thread while the GUI only receives finished results.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import battery, discovery, hidpp
from .autostart import Autostart
from .icon import make_icon

#: Charge drifts slowly, and every extra request wakes the mouse up.
POLL_INTERVAL_MS = 5 * 60 * 1000
#: Link lost — check more often so the device is picked up quickly once it returns.
OFFLINE_INTERVAL_MS = 60 * 1000
#: How long to wait for the device's name before showing a nameless icon.
#: Qt freezes the tray item's title at creation time, so the icon appears only
#: once the first poll has named the mouse. A live mouse answers in well under a
#: second; this cap only matters when there is none, and an icon reading
#: "no connection" is better than no icon at all.
NAME_WAIT_MS = 10 * 1000

FALLBACK_TITLE = "mousebat"


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
    """Lives on the worker thread, holding the transport and cache between polls."""

    sampled = pyqtSignal(object)  # Sample

    def __init__(self) -> None:
        super().__init__()
        self._transport: hidpp.Transport | None = None
        self._reader: battery.BatteryReader | None = None
        self._mouse: discovery.MouseDevice | None = None

    @pyqtSlot()
    def poll(self) -> None:
        try:
            self.sampled.emit(self._sample())
        except Exception as exc:  # noqa: BLE001 — a poll must never kill the thread
            self._drop()
            self.sampled.emit(Sample(name=None, reading=None, detail=str(exc)))

    def _sample(self) -> Sample:
        if self._reader is None and not self._connect():
            return Sample(name=None, reading=None, detail="no mouse found")

        assert self._reader is not None and self._mouse is not None
        try:
            return Sample(name=self._mouse.name, reading=self._reader.read())
        except (hidpp.HidppTimeout, hidpp.DeviceNotConnected, hidpp.HidppError, OSError) as exc:
            # The mouse may have gone to sleep, or the receiver moved to another
            # node, so start the search over.
            name = self._mouse.name
            self._drop()
            return Sample(name=name, reading=None, detail=str(exc))

    def _connect(self) -> bool:
        mouse = discovery.find_first_mouse()
        if mouse is None:
            return False
        try:
            transport = hidpp.Transport(mouse.device_path)
        except OSError:
            return False
        self._transport = transport
        self._mouse = mouse
        self._reader = battery.BatteryReader(hidpp.Link(transport), mouse.device_index)
        return True

    def _drop(self) -> None:
        if self._reader is not None:
            self._reader.forget()
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._reader = None
        self._mouse = None

    @pyqtSlot()
    def shutdown(self) -> None:
        self._drop()


class Tray(QObject):
    """Wires together the icon, the menu and the poll timer."""

    poll_requested = pyqtSignal()

    def __init__(self, app: QApplication, autostart: Autostart | None = None) -> None:
        super().__init__()
        self._app = app
        self._last_name: str | None = None
        self._autostart = autostart if autostart is not None else Autostart()

        # The icon is created later, in _ensure_icon: its title is fixed at
        # creation and we want the mouse's name in it.
        self._icon: QSystemTrayIcon | None = None

        menu = QMenu()
        self._refresh_action = menu.addAction("Refresh")
        self._refresh_action.triggered.connect(self.poll_requested.emit)

        self._autostart_action = menu.addAction("Start at login")
        self._autostart_action.setCheckable(True)
        self._autostart_action.toggled.connect(self._set_autostart)
        # Autostart may have been changed from a terminal meanwhile, so the
        # checkmark is refreshed every time the menu opens rather than once.
        menu.aboutToShow.connect(self._sync_autostart_action)
        self._sync_autostart_action()

        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(app.quit)
        self._menu = menu

        self._thread = QThread()
        self._poller = Poller()
        self._poller.moveToThread(self._thread)
        self._poller.sampled.connect(self._apply)
        self.poll_requested.connect(self._poller.poll)  # queued: runs on the worker
        app.aboutToQuit.connect(self._stop)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll_requested.emit)

    def _sync_autostart_action(self) -> None:
        """Mirror the unit's real state into the checkbox.

        Hidden when no unit is installed — the applet was started by hand, and
        there is nothing to enable.
        """
        available = self._autostart.available()
        self._autostart_action.setVisible(available)
        if not available:
            return
        enabled = self._autostart.enabled()
        # Assigning setChecked would re-emit toggled and call systemctl again.
        self._autostart_action.blockSignals(True)
        self._autostart_action.setChecked(enabled)
        self._autostart_action.blockSignals(False)

    @pyqtSlot(bool)
    def _set_autostart(self, value: bool) -> None:
        reached = self._autostart.set_enabled(value)
        if reached != value:
            # systemctl refused; show what is actually true.
            self._autostart_action.blockSignals(True)
            self._autostart_action.setChecked(reached)
            self._autostart_action.blockSignals(False)

    def _ensure_icon(self, name: str | None) -> QSystemTrayIcon:
        """Create the tray item once, titled after the mouse.

        Plasma shows this title in the collapsed-items list, and Qt copies it from
        the application name when the item is created — there is no way to change
        it afterwards, and re-creating the item makes it vanish from the tray for
        good. Hence: name first, icon second.
        """
        if self._icon is not None:
            return self._icon

        title = name or FALLBACK_TITLE
        self._app.setApplicationName(title)
        self._app.setApplicationDisplayName(title)

        self._icon = QSystemTrayIcon()
        self._icon.setIcon(make_icon(None, offline=True))
        self._icon.setToolTip(f"{title} — polling…")
        self._icon.setContextMenu(self._menu)
        self._icon.show()
        return self._icon

    def start(self) -> None:
        self._thread.start()
        self._timer.start()
        self.poll_requested.emit()
        # Nothing to name the icon after if no mouse ever answers, so show it anyway.
        QTimer.singleShot(NAME_WAIT_MS, lambda: self._ensure_icon(self._last_name))

    @pyqtSlot(object)
    def _apply(self, sample: Sample) -> None:
        if sample.name:
            self._last_name = sample.name
        name = self._last_name or "Mouse"
        icon_item = self._ensure_icon(sample.name)

        if sample.online:
            reading = sample.reading
            assert reading is not None
            percent_text = "—" if reading.percent is None else f"{reading.percent}%"
            icon_item.setIcon(make_icon(reading.percent, charging=reading.is_charging))
            icon_item.setToolTip(f"{name}\n{percent_text} — {reading.status.value}")
            self._timer.setInterval(POLL_INTERVAL_MS)
        else:
            icon_item.setIcon(make_icon(None, offline=True))
            detail = f"\n{sample.detail}" if sample.detail else ""
            icon_item.setToolTip(f"{name}\nno connection{detail}")
            self._timer.setInterval(OFFLINE_INTERVAL_MS)

    def _stop(self) -> None:
        self._timer.stop()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        # The thread has stopped, so closing the transport here is safe.
        self._poller.shutdown()
