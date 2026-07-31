"""The icon is checked pixel-wise: how much is painted, and in what colour."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets", reason="requires python3-pyqt6")

from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from mousebat import icon  # noqa: E402

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
    def test_healthy_charge_is_green(self) -> None:
        assert icon.color_for(50) == icon.COLOR_OK

    def test_full_charge_is_green(self) -> None:
        assert icon.color_for(100) == icon.COLOR_OK

    @pytest.mark.parametrize("percent", [19, 15, 10])
    def test_warning_range(self, percent: int) -> None:
        assert icon.color_for(percent) == icon.COLOR_WARN

    @pytest.mark.parametrize("percent", [9, 1, 0])
    def test_critical_range(self, percent: int) -> None:
        assert icon.color_for(percent) == icon.COLOR_CRITICAL

    def test_twenty_is_not_a_warning(self) -> None:
        assert icon.color_for(20) == icon.COLOR_OK

    def test_unknown_percent_uses_theme_color(self) -> None:
        """No data is not a charge level, so it must not read as healthy green."""
        assert icon.color_for(None) == icon.theme_color()
        assert icon.color_for(None) != icon.COLOR_OK


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


def opaque_runs(image, y: int) -> list[tuple[int, int]]:
    """Stretches of non-transparent pixels along row `y`."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for x in range(image.width()):
        opaque = image.pixelColor(x, y).alpha() > 0
        if opaque and start is None:
            start = x
        elif not opaque and start is not None:
            runs.append((start, x - 1))
            start = None
    if start is not None:
        runs.append((start, image.width() - 1))
    return runs


def top_wall_row(image) -> int:
    """A row that crosses the battery's top wall."""
    for y in range(image.height()):
        if any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width())):
            return y + 3
    raise AssertionError("the icon is empty")


class TestOutlineIntegrity:
    """The body must read as one shape — no gaps anywhere in it."""

    def test_fill_joins_the_outline_without_a_seam(self) -> None:
        """Half-full: left wall and fill form a single run, not two with a gap."""
        image = icon.render_pixmap(50, size=220, color=WHITE).toImage()
        runs = opaque_runs(image, image.height() // 2)
        # left wall + fill, right wall, nub
        assert len(runs) == 3

    def test_full_charge_is_one_solid_body(self) -> None:
        image = icon.render_pixmap(100, size=220, color=WHITE).toImage()
        runs = opaque_runs(image, image.height() // 2)
        # body, nub
        assert len(runs) == 2

    @pytest.mark.parametrize("percent", [100, 60, 30, 20, 7])
    def test_charging_never_breaks_the_top_wall(self, percent: int) -> None:
        """The bolt is clipped to the interior, so it cannot slice the walls."""
        image = icon.render_pixmap(percent, charging=True, size=220, color=WHITE).toImage()
        assert len(opaque_runs(image, top_wall_row(image))) == 1

    def test_charging_wall_matches_the_idle_one(self, ) -> None:
        idle = icon.render_pixmap(60, size=220, color=WHITE).toImage()
        charging = icon.render_pixmap(60, charging=True, size=220, color=WHITE).toImage()
        row = top_wall_row(idle)
        assert opaque_runs(charging, row) == opaque_runs(idle, row)


class TestCharging:
    def test_bolt_shows_beyond_a_short_fill(self) -> None:
        """At 20% the bolt stands past the fill as its own mark, so it stays readable."""
        idle = icon.render_pixmap(20, size=220, color=WHITE).toImage()
        charging = icon.render_pixmap(20, charging=True, size=220, color=WHITE).toImage()
        row = idle.height() // 2
        assert len(opaque_runs(charging, row)) > len(opaque_runs(idle, row))

    def test_charging_changes_the_picture(self) -> None:
        idle = icon.render_pixmap(60, size=220, color=WHITE).toImage()
        charging = icon.render_pixmap(60, charging=True, size=220, color=WHITE).toImage()
        assert idle != charging

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
