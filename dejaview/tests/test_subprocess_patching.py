import subprocess
from collections import defaultdict

import pytest

from dejaview.patching.custom_patchers import PopenPatcher
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


class TestSubprocessPatching:
    @pytest.mark.parametrize(
        "func_name, prefix",
        [
            ("run", "result"),
            ("check_output", "output"),
            ("check_call", "retcode"),
            ("call", "retcode"),
            ("getoutput", "out"),
            ("getstatusoutput", "status"),
        ],
    )
    def test_diverges_without_replay(self, monkeypatch, func_name, prefix):
        """Memoized replay vs. fresh call produce different results."""
        call_count = 0

        def _fake(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return f"{prefix}{call_count}"

        monkeypatch.setattr(f"subprocess.{func_name}", _fake)

        p = Patches()
        p.patch(subprocess, func_name)

        snap = capture()
        first = getattr(subprocess, func_name)("dummy")
        assert first == f"{prefix}1"

        # Replay: reset and call again → memoized value
        reset(snap)
        replayed = getattr(subprocess, func_name)("dummy")

        # Fresh: call again without resetting → new value
        fresh = getattr(subprocess, func_name)("dummy")

        assert replayed == f"{prefix}1", "replay should return the stored value"
        assert fresh == f"{prefix}2", "without replay, a new value is produced"
        assert replayed != fresh, "memoized replay and fresh call differ"

        p.__exit__(None, None, None)

    def test_popen_memoized(self):
        """Popen replay returns stored output even with different args."""
        p = Patches()
        p.patch(subprocess, "Popen", PopenPatcher)

        snap = capture()
        proc = subprocess.Popen(
            ["echo", "original"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, _ = proc.communicate()

        # Replay: different args, but memoized result returned
        reset(snap)
        replay_proc = subprocess.Popen(
            ["echo", "WRONG"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        replay_out, _ = replay_proc.communicate()

        assert replay_out == out
        assert b"original" in replay_out

        p.__exit__(None, None, None)

    def test_run_memoized_e2e(self):
        d = launch_dejaview(
            """
            import subprocess                                              # Line 1
            print()                                                        # Line 2
            print(subprocess.run(["echo", "hi"], capture_output=True))     # Line 3
            print()                                                        # Line 4
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

        assert "hi" in out1
        assert "hi" in out2

    def test_check_output_memoized_e2e(self):
        d = launch_dejaview(
            """
            import subprocess                                    # Line 1
            print()                                              # Line 2
            print(subprocess.check_output(["echo", "hello"]))   # Line 3
            print()                                              # Line 4
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

        assert "hello" in out1
        assert "hello" in out2
