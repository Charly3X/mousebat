#!/usr/bin/env python3
"""Render a contact sheet of every icon state into a single PNG.

Run:  QT_QPA_PLATFORM=offscreen python3 tools/preview_icon.py /tmp/preview.png
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mousebat import icon  # noqa: E402

STATES = (
    ("100%", 100, False, False),
    ("73%", 73, False, False),
    ("50%", 50, False, False),
    ("19% warn", 19, False, False),
    ("7% crit", 7, False, False),
    ("charging", 45, True, False),
    ("no link", None, False, True),
)

CELL = 96
LABEL = 22
PANEL_BG = QColor("#2a2e32")  # Plasma panel background, dark theme


def main(out_path: str) -> int:
    app = QApplication([])
    app.setPalette(app.palette())

    sheet = QImage(CELL * len(STATES), CELL + LABEL, QImage.Format.Format_ARGB32)
    sheet.fill(PANEL_BG)

    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for column, (label, percent, charging, offline) in enumerate(STATES):
        pixmap = icon.render_pixmap(
            percent, charging=charging, offline=offline, size=CELL - 16,
            color=QColor("#f4f4f4") if percent is None or percent >= 20 else None,
        )
        painter.drawPixmap(column * CELL + 8, 8, pixmap)
        painter.setPen(QColor("#c8c8c8"))
        painter.drawText(
            QRectF(column * CELL, CELL, CELL, LABEL),
            int(Qt.AlignmentFlag.AlignCenter),
            label,
        )
    painter.end()

    sheet.save(out_path)
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/mousebat-preview.png"))
