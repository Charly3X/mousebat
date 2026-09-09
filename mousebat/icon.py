"""Drawing the battery icon.

Knows nothing about devices or polling: percentage and status in, QIcon out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPixmap, QPolygonF
from PyQt6.QtWidgets import QApplication

#: Colour thresholds: strictly below these values.
WARN_BELOW = 20
CRITICAL_BELOW = 10

COLOR_OK = QColor("#3fbf5f")
COLOR_WARN = QColor("#e8b010")
COLOR_CRITICAL = QColor("#e04a3f")
COLOR_FALLBACK = QColor("#dcdcdc")

#: Sizes baked into the QIcon — the panel picks whichever fits.
ICON_SIZES = (22, 32, 44, 64)


class Shape(Enum):
    """Which silhouette carries the charge."""

    BATTERY = "battery"
    KEYBOARD = "keyboard"
    MOUSE = "mouse"


@dataclass(frozen=True)
class Silhouette:
    """A shape's geometry in pixels, for one icon size.

    outline — stroked with the charge colour.
    interior — the area the charge fills, already inset by half a pen so the fill
        meets the outline's inner face without a seam.
    solid — painted filled rather than stroked (the battery's nub).
    vertical — fill grows upwards from the bottom instead of rightwards.
    """

    outline: QPainterPath
    interior: QRectF
    solid: QPainterPath | None = None
    vertical: bool = False


def _battery_silhouette(unit: float, pen: float) -> Silhouette:
    body = QRectF(2 * unit, 6 * unit, 16 * unit, 10 * unit)
    outline = QPainterPath()
    outline.addRect(body)
    nose = QPainterPath()
    nose.addRect(
        QRectF(
            body.right() + pen,
            body.top() + body.height() * 0.28,
            1.8 * unit,
            body.height() * 0.44,
        )
    )
    half = pen / 2.0
    return Silhouette(
        outline=outline, interior=body.adjusted(half, half, -half, -half), solid=nose
    )


def _keyboard_silhouette(unit: float, pen: float) -> Silhouette:
    """A wide slab, barely rounded. No key marks.

    A row of little keys is what a keyboard has, but at panel size it renders as a
    dashed line rather than as keys, which reads as noise. The slab carries the
    identity by contrast with the mouse's profile instead.
    """
    body = QRectF(1.5 * unit, 7.0 * unit, 19 * unit, 8 * unit)
    outline = QPainterPath()
    outline.addRoundedRect(body, 0.9 * unit, 0.9 * unit)
    half = pen / 2.0
    return Silhouette(outline=outline, interior=body.adjusted(half, half, -half, -half))


#: The mouse keeps the classic battery, nub and all; the keyboard is the nubless slab.
#: Drawing the devices themselves was tried and dropped — a mouse is taller than wide
#: seen from above, which clashed with the keyboard's orientation, and in profile it
#: read as an abstract wedge. A shape that looks deliberate beats one that looks like
#: a failed drawing. An unrecognised device shares the mouse's look, which is harmless:
#: the tooltip names it.
_SILHOUETTES = {
    Shape.BATTERY: _battery_silhouette,
    Shape.KEYBOARD: _keyboard_silhouette,
    Shape.MOUSE: _battery_silhouette,
}


def _fill_rect(interior: QRectF, percent: int, pen: float, vertical: bool) -> QRectF:
    """The filled portion of the interior, never thinner than a pen stroke."""
    if vertical:
        height = max(interior.height() * percent / 100.0, pen)
        return QRectF(interior.left(), interior.bottom() - height, interior.width(), height)
    width = max(interior.width() * percent / 100.0, pen)
    return QRectF(interior.left(), interior.top(), width, interior.height())


def theme_color() -> QColor:
    """The theme's regular colour, used when the charge is unknown or the link is down.

    Falls back to light grey without a QApplication, so the icon can be rendered
    headless (tests, tools/preview_icon.py).
    """
    app = QApplication.instance()
    if app is None:
        return QColor(COLOR_FALLBACK)
    return app.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText)


def color_for(percent: int | None) -> QColor:
    """Green from 20% up, amber below 20%, red below 10%.

    An unknown percentage gets the theme colour: it is a "no data" state rather
    than a charge level, and should not read as healthy green.
    """
    if percent is None:
        return theme_color()
    if percent < CRITICAL_BELOW:
        return QColor(COLOR_CRITICAL)
    if percent < WARN_BELOW:
        return QColor(COLOR_WARN)
    return QColor(COLOR_OK)


def _bolt(rect: QRectF) -> QPolygonF:
    """A lightning bolt fitted into the fill rectangle."""
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
    shape: Shape = Shape.BATTERY,
    charging: bool = False,
    offline: bool = False,
    size: int = 64,
    color: QColor | None = None,
) -> QPixmap:
    """The device's silhouette, filled in proportion to its charge."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    stroke = color_for(percent) if color is None else QColor(color)
    if offline:
        stroke.setAlphaF(0.45)

    unit = size / 22.0  # proportions are authored for a 22x22 panel icon
    pen_width = max(1.0, round(1.6 * unit))
    silhouette = _SILHOUETTES[shape](unit, pen_width)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        pen = painter.pen()
        pen.setColor(stroke)
        pen.setWidthF(pen_width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(silhouette.outline)

        if silhouette.solid is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(stroke)
            painter.drawPath(silhouette.solid)

        if not offline and percent is not None and percent > 0:
            # A pen straddles the shape's edge, so the outline's inner face sits half
            # a pen inwards; `interior` is already inset by that much, which leaves no
            # seam between fill and outline.
            inner = silhouette.interior
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(stroke)
            # Curved outlines — the mouse's crown and nose — would let a plain
            # rectangle spill past the shape, so the fill is clipped to the outline.
            painter.setClipPath(silhouette.outline)
            painter.drawRect(_fill_rect(inner, percent, pen_width, silhouette.vertical))
            painter.setClipping(False)

            if charging:
                # Clipping to the inner area keeps the cut away from the outline —
                # without it the bolt slices through the top and bottom walls.
                painter.setClipRect(inner)
                bolt = _bolt(inner)

                # Cut a slightly wider silhouette first, so the bolt reads as a gap
                # in the fill instead of merging with it...
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                gap_pen = painter.pen()
                gap_pen.setColor(QColor(Qt.GlobalColor.black))
                gap_pen.setWidthF(pen_width)
                painter.setPen(gap_pen)
                painter.setBrush(QColor(Qt.GlobalColor.black))
                painter.drawPolygon(bolt)

                # ...then paint the bolt itself, so it stays visible even when the
                # fill is too short to contain it.
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(stroke)
                painter.drawPolygon(bolt)
                painter.setClipping(False)
    finally:
        painter.end()
    return pixmap


def make_icon(
    percent: int | None,
    *,
    shape: Shape = Shape.BATTERY,
    charging: bool = False,
    offline: bool = False,
) -> QIcon:
    """A QIcon carrying every panel size."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(
            render_pixmap(
                percent, shape=shape, charging=charging, offline=offline, size=size
            )
        )
    return icon
