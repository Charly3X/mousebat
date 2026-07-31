"""The tray icon, plus device polling on a worker thread.

Polling blocks: with the link lost, walking receivers and indices takes seconds,
so it lives on a worker thread while the GUI only receives finished results.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import battery, discovery, hidpp
from .icon import make_icon

#: Charge drifts slowly, and every extra request wakes the mouse up.
POLL_INTERVAL_MS = 5 * 60 * 1000
#: Link lost — check more often so the device is picked up quickly once it returns.
OFFLINE_INTERVAL_MS = 60 * 1000


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

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._last_name: str | None = None

        self._icon = QSystemTrayIcon()
        self._icon.setIcon(make_icon(None, offline=True))
        self._icon.setToolTip("mousebat — polling…")

        menu = QMenu()
        self._refresh_action = menu.addAction("Refresh")
        self._refresh_action.triggered.connect(self.poll_requested.emit)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(app.quit)
        self._icon.setContextMenu(menu)
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

    def start(self) -> None:
        self._thread.start()
        self._icon.show()
        self._timer.start()
        self.poll_requested.emit()

    @pyqtSlot(object)
    def _apply(self, sample: Sample) -> None:
        if sample.name:
            self._last_name = sample.name
        name = self._last_name or "Mouse"

        if sample.online:
            reading = sample.reading
            assert reading is not None
            percent_text = "—" if reading.percent is None else f"{reading.percent}%"
            self._icon.setIcon(
                make_icon(reading.percent, charging=reading.is_charging)
            )
            self._icon.setToolTip(f"{name}\n{percent_text} — {reading.status.value}")
            self._timer.setInterval(POLL_INTERVAL_MS)
        else:
            self._icon.setIcon(make_icon(None, offline=True))
            detail = f"\n{sample.detail}" if sample.detail else ""
            self._icon.setToolTip(f"{name}\nno connection{detail}")
            self._timer.setInterval(OFFLINE_INTERVAL_MS)

    def _stop(self) -> None:
        self._timer.stop()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        # The thread has stopped, so closing the transport here is safe.
        self._poller.shutdown()
