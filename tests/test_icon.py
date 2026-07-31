"""The icon is checked pixel-wise: how much is painted, and in what colour."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="requires python3-pyqt6")

from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from battary import icon  # noqa: E402

WHITE = QColor("#ffffff")


@pytest.fixture(scope="module", autouse=True)
def app() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def filled_columns(percent: int | None, *, charging: bool = False, offline: bool = False) -> int:
    """How many columns are opaque — a proxy for the fill ratio."""
    image = icon.render_pixmap(
        percent, charging=charging, offline=offline, size=220, color=WHITE
    ).toImage()
    middle = image.height() // 2
    return sum(
        1
        for x in range(image.width())
        if image.pixelColor(x, middle).alpha() > 0
    )


class TestColorThresholds:
    def test_normal_charge_uses_theme_color(self) -> None:
        assert icon.color_for(50) == icon.theme_color()

    @pytest.mark.parametrize("percent", [19, 15, 10])
    def test_warning_range(self, percent: int) -> None:
        assert icon.color_for(percent) == icon.COLOR_WARN

    @pytest.mark.parametrize("percent", [9, 1, 0])
    def test_critical_range(self, percent: int) -> None:
        assert icon.color_for(percent) == icon.COLOR_CRITICAL

    def test_twenty_is_not_a_warning(self) -> None:
        assert icon.color_for(20) == icon.theme_color()

    def test_unknown_percent_uses_theme_color(self) -> None:
        assert icon.color_for(None) == icon.theme_color()


class TestFill:
    def test_fill_grows_with_percent(self) -> None:
        low, mid, high = filled_columns(10), filled_columns(50), filled_columns(95)
        assert low < mid < high

    def test_full_charge_fills_more_than_half_of_the_body(self) -> None:
        image = icon.render_pixmap(100, size=220, color=WHITE).toImage()
        middle = image.height() // 2
        opaque = sum(
            1 for x in range(image.width()) if image.pixelColor(x, middle).alpha() > 0
        )
        assert opaque > image.width() * 0.5

    def test_zero_percent_draws_only_outline(self) -> None:
        """At 0% there is no fill — only the body walls and the nub show."""
        assert filled_columns(0) < filled_columns(50)

    def test_offline_is_dimmed(self) -> None:
        online = icon.render_pixmap(50, size=220, color=WHITE).toImage()
        offline = icon.render_pixmap(50, offline=True, size=220, color=WHITE).toImage()
        row = online.height() // 2
        online_alpha = max(online.pixelColor(x, row).alpha() for x in range(online.width()))
        offline_alpha = max(offline.pixelColor(x, row).alpha() for x in range(offline.width()))
        assert offline_alpha < online_alpha


class TestCharging:
    def test_bolt_is_cut_out_of_the_fill(self) -> None:
        plain = icon.render_pixmap(100, size=220, color=WHITE).toImage()
        charging = icon.render_pixmap(100, charging=True, size=220, color=WHITE).toImage()

        def opaque_pixels(image) -> int:
            return sum(
                1
                for y in range(image.height())
                for x in range(image.width())
                if image.pixelColor(x, y).alpha() > 0
            )

        assert opaque_pixels(charging) < opaque_pixels(plain)

    def test_no_bolt_when_not_charging(self) -> None:
        first = icon.render_pixmap(60, size=220, color=WHITE).toImage()
        second = icon.render_pixmap(60, charging=False, size=220, color=WHITE).toImage()
        assert first == second


class TestMakeIcon:
    def test_contains_all_panel_sizes(self) -> None:
        available = {size.width() for size in icon.make_icon(50).availableSizes()}
        assert set(icon.ICON_SIZES) <= available

    def test_offline_icon_is_not_empty(self) -> None:
        assert not icon.make_icon(None, offline=True).isNull()
