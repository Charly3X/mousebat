"""Autostart is tested against a fake systemctl — the real one is never called."""

from __future__ import annotations

from mousebat.autostart import UNIT_NAME, Autostart


class FakeSystemctl:
    """Records invocations and answers from a scripted state."""

    def __init__(self, *, installed: bool = True, enabled: bool = False) -> None:
        self.installed = installed
        self.state = enabled
        self.calls: list[list[str]] = []
        self.refuse = False

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(args)
        verb = args[2]
        if verb == "list-unit-files":
            if not self.installed:
                return 0, "0 unit files listed."
            return 0, f"UNIT FILE       STATE\n{UNIT_NAME}  enabled\n"
        if verb == "is-enabled":
            return (0, "enabled") if self.state else (1, "disabled")
        if verb in ("enable", "disable"):
            if not self.refuse:
                self.state = verb == "enable"
            return 0, ""
        raise AssertionError(f"unexpected verb: {verb}")

    @property
    def verbs(self) -> list[str]:
        return [call[2] for call in self.calls]


class TestAvailable:
    def test_true_when_unit_is_listed(self) -> None:
        assert Autostart(runner=FakeSystemctl()).available()

    def test_false_when_no_unit_installed(self) -> None:
        assert not Autostart(runner=FakeSystemctl(installed=False)).available()

    def test_false_when_systemctl_is_missing(self) -> None:
        def explode(args: list[str]) -> tuple[int, str]:
            raise FileNotFoundError("systemctl")

        assert not Autostart(runner=explode).available()


class TestEnabled:
    def test_reads_enabled(self) -> None:
        assert Autostart(runner=FakeSystemctl(enabled=True)).enabled()

    def test_reads_disabled(self) -> None:
        assert not Autostart(runner=FakeSystemctl(enabled=False)).enabled()

    def test_nonzero_exit_still_yields_a_verdict(self) -> None:
        """`is-enabled` exits 1 for a disabled unit; the word decides, not the code."""
        fake = FakeSystemctl(enabled=False)
        assert Autostart(runner=fake).enabled() is False
        assert fake.verbs == ["is-enabled"]

    def test_empty_output_is_treated_as_disabled(self) -> None:
        assert not Autostart(runner=lambda args: (1, "")).enabled()


class TestSetEnabled:
    def test_turning_on(self) -> None:
        fake = FakeSystemctl(enabled=False)
        assert Autostart(runner=fake).set_enabled(True) is True
        assert "enable" in fake.verbs

    def test_turning_off(self) -> None:
        fake = FakeSystemctl(enabled=True)
        assert Autostart(runner=fake).set_enabled(False) is False
        assert "disable" in fake.verbs

    def test_reports_the_state_actually_reached(self) -> None:
        """When systemctl refuses, the caller learns the real state."""
        fake = FakeSystemctl(enabled=False)
        fake.refuse = True
        assert Autostart(runner=fake).set_enabled(True) is False

    def test_targets_our_unit(self) -> None:
        fake = FakeSystemctl()
        Autostart(runner=fake).set_enabled(True)
        assert all(call[-1] == UNIT_NAME for call in fake.calls)
