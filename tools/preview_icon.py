#!/usr/bin/env python3
"""Render a contact sheet of every icon state into a single PNG.

This produces the image shown in README.md, straight from the rendering code, so the
documentation cannot drift away from what the applet actually draws.

Run:  QT_QPA_PLATFORM=offscreen python3 tools/preview_icon.py [output.png]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mousebat import icon  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "docs", "images", "icon-states.png")

LEVELS = (("100%", 100), ("73%", 73), ("50%", 50), ("19%", 19), ("7%", 7))
ROWS = (
    ("idle", False, (*LEVELS, ("no link", None))),
    ("charging", True, LEVELS),
)

CELL = 88
LABEL = 20
PAD = 12
LEGEND = 84
PANEL_BG = QColor("#2a2e32")  # Plasma panel background, dark theme
#: Without a live palette the dimmed state would be invisible against the panel.
OFFLINE_COLOR = QColor("#8a8f94")


def main(out_path: str) -> int:
    # Kept in a local: dropping the reference would collect the application and
    # take the font database down with it.
    app = QApplication([])
    assert app is not None

    columns = max(len(states) for _, _, states in ROWS)
    width = LEGEND + columns * CELL + PAD
    height = PAD + len(ROWS) * (CELL + LABEL) + PAD

    sheet = QImage(width, height, QImage.Format.Format_ARGB32)
    sheet.fill(PANEL_BG)

    legend_font = QFont()
    legend_font.setPointSize(10)
    legend_font.setBold(True)
    label_font = QFont()
    label_font.setPointSize(9)

    painter = QPainter(sheet)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        for row, (row_label, charging, states) in enumerate(ROWS):
            y = PAD + row * (CELL + LABEL)

            painter.setFont(legend_font)
            painter.setPen(QColor("#8a9099"))
            painter.drawText(
                QRectF(PAD, y, LEGEND - PAD, CELL),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                row_label,
            )

            for column, (label, percent) in enumerate(states):
                x = LEGEND + column * CELL
                offline = percent is None
                pixmap = icon.render_pixmap(
                    percent,
                    charging=charging and not offline,
                    offline=offline,
                    size=CELL - 24,
                    color=OFFLINE_COLOR if offline else None,
                )
                painter.drawPixmap(int(x + 12), int(y + 12), pixmap)

                painter.setFont(label_font)
                painter.setPen(QColor("#c3c8ce"))
                painter.drawText(
                    QRectF(x, y + CELL - 6, CELL, LABEL),
                    int(Qt.AlignmentFlag.AlignCenter),
                    label,
                )
    finally:
        painter.end()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if not sheet.save(out_path):
        print(f"could not write {out_path}", file=sys.stderr)
        return 1
    print(f"written: {out_path} ({sheet.width()}x{sheet.height()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT))
