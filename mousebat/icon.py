"""Drawing the battery icon.

Knows nothing about devices or polling: percentage and status in, QIcon out.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap, QPolygonF
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
    charging: bool = False,
    offline: bool = False,
    size: int = 64,
    color: QColor | None = None,
) -> QPixmap:
    """A horizontal battery: outline, nub on the right, fill proportional to charge."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    stroke = color_for(percent) if color is None else QColor(color)
    if offline:
        stroke.setAlphaF(0.45)

    unit = size / 22.0  # proportions are authored for a 22x22 panel icon
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
            # A pen straddles the rectangle's edge, so the outline's inner face sits
            # half a pen inwards. Starting the fill exactly there leaves no seam
            # between fill and outline.
            half = pen_width / 2.0
            inner = body.adjusted(half, half, -half, -half)
            fill = QRectF(
                inner.left(),
                inner.top(),
                max(inner.width() * percent / 100.0, pen_width),
                inner.height(),
            )
            painter.drawRect(fill)

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
    percent: int | None, *, charging: bool = False, offline: bool = False
) -> QIcon:
    """A QIcon carrying every panel size."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(
            render_pixmap(percent, charging=charging, offline=offline, size=size)
        )
    return icon
