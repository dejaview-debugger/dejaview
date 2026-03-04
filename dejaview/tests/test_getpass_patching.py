import getpass
from collections import defaultdict

import pytest

from dejaview.patching.patching import (
    Patches,
    capture,
    capture_funcs,
    reset,
    reset_funcs,
)
from dejaview.patching.state_store import FunctionStateStore, StateStore
from dejaview.tests.util import launch_dejaview


@pytest.fixture(autouse=True)
def _clean_global_state():
    old_capture = list(capture_funcs)
    old_reset = list(reset_funcs)
    old_store = StateStore.store

    StateStore.store = defaultdict(FunctionStateStore)

    yield

    capture_funcs.clear()
    capture_funcs.extend(old_capture)
    reset_funcs.clear()
    reset_funcs.extend(old_reset)
    StateStore.store = old_store


class TestGetpassPatching:
    def test_getpass_memoized_value(self):
        d = launch_dejaview(
            """
            import getpass           # Line 1
            print()                  # Line 2
            print(getpass.getpass()) # Line 3
            print()                  # Line 4
            """
        )

        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)

        # Execute line 3 — getpass prompts for input
        d.sendline("n")
        d.expect_exact("Password: ")
        d.sendline("my_secret")
        out1 = d.assert_line_number(4)

        # Replay line 3 — memoized, no prompt
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("n")
        out2 = d.assert_line_number(4)
        d.quit()

        assert "my_secret" in out1
        assert "my_secret" in out2

    def test_getuser_memoized_value(self):
        d = launch_dejaview(
            """
            import getpass            # Line 1
            print()                   # Line 2
            print(getpass.getuser())  # Line 3
            print()                   # Line 4
            """
        )

        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)

        # Execute line 3
        d.sendline("n")
        out1 = d.assert_line_number(4)

        # Replay line 3 — memoized
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("n")
        out2 = d.assert_line_number(4)
        d.quit()

        username = getpass.getuser()
        assert username in out1
        assert username in out2

    @pytest.mark.parametrize(
        "func_name, prefix",
        [
            ("getpass", "secret"),
            ("getuser", "user"),
        ],
    )
    def test_diverges_without_replay(self, monkeypatch, func_name, prefix):
        """Memoized replay vs. fresh call produce different results,
        proving memoization actually changes the return value."""
        call_count = 0

        def _fake(**_kwargs):
            nonlocal call_count
            call_count += 1
            return f"{prefix}{call_count}"

        monkeypatch.setattr(f"getpass.{func_name}", _fake)

        p = Patches()
        p.patch(getpass, func_name)

        snap = capture()
        first = getattr(getpass, func_name)()
        assert first == f"{prefix}1"

        # Replay: reset and call again → memoized value
        reset(snap)
        replayed = getattr(getpass, func_name)()

        # Fresh: call again without resetting → new value
        fresh = getattr(getpass, func_name)()

        assert replayed == f"{prefix}1", "replay should return the stored value"
        assert fresh == f"{prefix}2", "without replay, a new value is produced"
        assert replayed != fresh, "memoized replay and fresh call differ"

        p.__exit__(None, None, None)
