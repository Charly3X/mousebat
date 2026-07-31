"""Отрисовка иконки батарейки.

Ничего не знает про устройства и опрос: на входе процент и статус, на выходе QIcon.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap, QPolygonF
from PyQt6.QtWidgets import QApplication

#: Пороги смены цвета: строго ниже этих значений.
WARN_BELOW = 20
CRITICAL_BELOW = 10

COLOR_WARN = QColor("#e8b010")
COLOR_CRITICAL = QColor("#e04a3f")
COLOR_FALLBACK = QColor("#dcdcdc")

#: Размеры, которые кладём в QIcon — панель выберет подходящий.
ICON_SIZES = (22, 32, 44, 64)


def theme_color() -> QColor:
    """Обычный цвет темы для иконки; без QApplication — светло-серый."""
    app = QApplication.instance()
    if app is None:
        return QColor(COLOR_FALLBACK)
    return app.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)


def color_for(percent: int | None) -> QColor:
    """Жёлтый ниже 20%, красный ниже 10%, иначе цвет темы."""
    if percent is None:
        return theme_color()
    if percent < CRITICAL_BELOW:
        return QColor(COLOR_CRITICAL)
    if percent < WARN_BELOW:
        return QColor(COLOR_WARN)
    return theme_color()


def _bolt(rect: QRectF) -> QPolygonF:
    """Молния, вписанная в прямоугольник заливки."""
    left, top = rect.left(), rect.top()
    width, height = rect.width(), rect.height()
    points = (
        (0.58, 0.0),
        (0.24, 0.55),
        (0.46, 0.55),
        (0.38, 1.0),
        (0.76, 0.42),
        (0.52, 0.42),
    )
    return QPolygonF([QPointF(left + x * width, top + y * height) for x, y in points])


def render_pixmap(
    percent: int | None,
    *,
    charging: bool = False,
    offline: bool = False,
    size: int = 64,
    color: QColor | None = None,
) -> QPixmap:
    """Горизонтальная батарейка: контур, носик справа, заливка по проценту."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    stroke = color_for(percent) if color is None else QColor(color)
    if offline:
        stroke.setAlphaF(0.45)

    unit = size / 22.0  # рисуем в пропорциях панельных 22×22
    pen_width = max(1.0, round(1.6 * unit))

    body = QRectF(
        2 * unit,
        6 * unit,
        16 * unit,
        10 * unit,
    )
    nose = QRectF(
        body.right() + pen_width,
        body.top() + body.height() * 0.28,
        1.8 * unit,
        body.height() * 0.44,
    )

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        pen = painter.pen()
        pen.setColor(stroke)
        pen.setWidthF(pen_width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(body)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(stroke)
        painter.drawRect(nose)

        if not offline and percent is not None and percent > 0:
            inset = pen_width
            usable = body.adjusted(inset, inset, -inset, -inset)
            fill = QRectF(
                usable.left(),
                usable.top(),
                max(usable.width() * percent / 100.0, pen_width),
                usable.height(),
            )
            painter.drawRect(fill)

            if charging:
                # Молния вырезается из заливки: видна как «дырка» цвета фона.
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.drawPolygon(_bolt(body.adjusted(0, -unit, 0, unit)))
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    finally:
        painter.end()
    return pixmap


def make_icon(
    percent: int | None, *, charging: bool = False, offline: bool = False
) -> QIcon:
    """QIcon со всеми панельными размерами."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(
            render_pixmap(percent, charging=charging, offline=offline, size=size)
        )
    return icon
