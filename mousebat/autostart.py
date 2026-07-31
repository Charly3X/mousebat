"""Autostart state, expressed as the systemd user unit's enablement.

The unit is the single source of truth: there is no separate setting to drift out
of sync with it. Every systemctl call goes through an injectable runner, so the
tray logic is testable without touching the real service manager.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

UNIT_NAME = "mousebat.service"

#: (returncode, stdout) of a finished command.
Runner = Callable[[list[str]], tuple[int, str]]


def _run(args: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        args, capture_output=True, text=True, timeout=10, check=False
    )
    return completed.returncode, completed.stdout.strip()


@dataclass
class Autostart:
    """Reads and flips `systemctl --user enable/disable` for our unit."""

    unit: str = UNIT_NAME
    runner: Runner = _run

    def _systemctl(self, *args: str) -> tuple[int, str]:
        try:
            return self.runner(["systemctl", "--user", *args])
        except (OSError, subprocess.SubprocessError):
            # No systemd in this session, or systemctl is missing entirely.
            return 1, ""

    def available(self) -> bool:
        """Whether the unit is installed at all.

        False when the applet was started by hand from a checkout: there is
        nothing to enable, so the tray hides the toggle instead of offering an
        action that cannot work.
        """
        code, out = self._systemctl("list-unit-files", self.unit)
        return code == 0 and self.unit in out

    def enabled(self) -> bool:
        # `is-enabled` exits non-zero for disabled units, so the word matters,
        # not the exit code.
        _, out = self._systemctl("is-enabled", self.unit)
        return out.splitlines()[0].strip() == "enabled" if out else False

    def set_enabled(self, value: bool) -> bool:
        """Enable or disable autostart. Returns the state actually reached."""
        self._systemctl("enable" if value else "disable", self.unit)
        return self.enabled()
