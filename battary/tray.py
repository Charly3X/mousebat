"""Иконка в трее и опрос устройства в отдельном потоке.

Опрос блокирующий: при потерянной связи перебор ресиверов и индексов занимает
секунды, поэтому он живёт в рабочем потоке, а GUI только принимает готовый результат.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import battery, discovery, hidpp
from .icon import make_icon

#: Заряд меняется медленно, а лишние запросы будят мышь.
POLL_INTERVAL_MS = 5 * 60 * 1000
#: Потеряли связь — проверяем чаще, чтобы быстрее подхватить возврат.
OFFLINE_INTERVAL_MS = 60 * 1000


@dataclass(frozen=True)
class Sample:
    """Результат одного опроса."""

    name: str | None
    reading: battery.BatteryReading | None
    detail: str = ""

    @property
    def online(self) -> bool:
        return self.reading is not None


class Poller(QObject):
    """Живёт в рабочем потоке. Держит транспорт и кэш между опросами."""

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
        except Exception as exc:  # noqa: BLE001 — опрос не должен ронять поток
            self._drop()
            self.sampled.emit(Sample(name=None, reading=None, detail=str(exc)))

    def _sample(self) -> Sample:
        if self._reader is None and not self._connect():
            return Sample(name=None, reading=None, detail="мышь не найдена")

        assert self._reader is not None and self._mouse is not None
        try:
            return Sample(name=self._mouse.name, reading=self._reader.read())
        except (hidpp.HidppTimeout, hidpp.DeviceNotConnected, hidpp.HidppError, OSError) as exc:
            # Мышь могла уснуть или ресивер сменить узел — начинаем поиск заново.
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
    """Собирает иконку, меню и таймер опроса."""

    poll_requested = pyqtSignal()

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._last_name: str | None = None

        self._icon = QSystemTrayIcon()
        self._icon.setIcon(make_icon(None, offline=True))
        self._icon.setToolTip("battary — опрос…")

        menu = QMenu()
        self._refresh_action = menu.addAction("Обновить")
        self._refresh_action.triggered.connect(self.poll_requested.emit)
        menu.addSeparator()
        menu.addAction("Выход").triggered.connect(app.quit)
        self._icon.setContextMenu(menu)
        self._menu = menu

        self._thread = QThread()
        self._poller = Poller()
        self._poller.moveToThread(self._thread)
        self._poller.sampled.connect(self._apply)
        self.poll_requested.connect(self._poller.poll)  # queued: исполнится в потоке
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
        name = self._last_name or "Мышь"

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
            self._icon.setToolTip(f"{name}\nнет связи{detail}")
            self._timer.setInterval(OFFLINE_INTERVAL_MS)

    def _stop(self) -> None:
        self._timer.stop()
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        # Поток уже остановлен, поэтому транспорт закрываем прямо здесь.
        self._poller.shutdown()
