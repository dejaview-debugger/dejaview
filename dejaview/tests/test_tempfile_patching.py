import io
import os
import tempfile
from collections import defaultdict

import pytest

from dejaview.patching.custom_patchers import TempDirPatcher, TempFilePatcher
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


class TestTempfilePatching:
    @pytest.mark.parametrize(
        "func_name, prefix",
        [
            ("mkdtemp", "dir"),
            ("mkstemp", "file"),
            ("gettempdir", "tmp"),
        ],
    )
    def test_diverges_without_replay(self, monkeypatch, func_name, prefix):
        """Memoized replay vs. fresh call produce different results."""
        call_count = 0

        def _fake(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return f"{prefix}{call_count}"

        monkeypatch.setattr(f"tempfile.{func_name}", _fake)

        p = Patches()
        p.patch(tempfile, func_name)

        snap = capture()
        first = getattr(tempfile, func_name)()
        assert first == f"{prefix}1"

        # Replay: reset and call again → memoized value
        reset(snap)
        replayed = getattr(tempfile, func_name)()

        # Fresh: call again without resetting → new value
        fresh = getattr(tempfile, func_name)()

        assert replayed == f"{prefix}1", "replay should return the stored value"
        assert fresh == f"{prefix}2", "without replay, a new value is produced"
        assert replayed != fresh, "memoized replay and fresh call differ"

        p.__exit__(None, None, None)

    def test_named_tempfile_memoized(self):
        """NamedTemporaryFile returns in-memory buffer on replay with same name."""
        p = Patches()
        p.patch(tempfile, "NamedTemporaryFile", TempFilePatcher)

        snap = capture()
        tmp = tempfile.NamedTemporaryFile(suffix=".play", delete=False)
        name = tmp.name
        tmp.close()

        reset(snap)
        replay_tmp = tempfile.NamedTemporaryFile(suffix=".replay", delete=False)
        assert isinstance(replay_tmp, io.BytesIO)
        assert replay_tmp.name == name

        os.unlink(name)
        p.__exit__(None, None, None)

    def test_temporary_directory_memoized(self):
        """TemporaryDirectory returns same name on replay despite different prefix."""
        p = Patches()
        p.patch(tempfile, "TemporaryDirectory", TempDirPatcher)

        snap = capture()
        with tempfile.TemporaryDirectory(prefix="play_") as play_name:
            assert os.path.isdir(play_name)

        reset(snap)
        with tempfile.TemporaryDirectory(prefix="replay_") as replay_name:
            assert play_name == replay_name

        p.__exit__(None, None, None)

    def test_mkdtemp_memoized_e2e(self):
        d = launch_dejaview(
            """
            import tempfile              # Line 1
            print()                      # Line 2
            print(tempfile.mkdtemp())    # Line 3
            print()                      # Line 4
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

        assert "/tmp/" in out1
        assert "/tmp/" in out2

    def test_gettempdir_memoized_e2e(self):
        d = launch_dejaview(
            """
            import tempfile                # Line 1
            print()                        # Line 2
            print(tempfile.gettempdir())   # Line 3
            print()                        # Line 4
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

        expected = tempfile.gettempdir()
        assert expected in out1
        assert expected in out2
