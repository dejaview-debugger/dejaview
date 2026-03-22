import ast
import operator
import os as _real_os
import tempfile
from pathlib import Path
from typing import cast as type_cast

from dejaview.tests.util import (
    DebugCommand,
    PropertyTester,
    launch_dejaview,
    verify_deterministic_memoized_value_util,
    verify_deterministic_mutated_value_util,
)

# ==============================================================================
# Process / user identity
# ==============================================================================


def test_getpid():
    """Test that os.getpid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getpid()",
        compare=operator.eq,
    )


# ==============================================================================
# Filesystem queries
# ==============================================================================


def test_listdir():
    """Test that os.listdir is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "a.txt").touch()
        Path(tmpdir, "b.txt").touch()

        before, after = verify_deterministic_mutated_value_util(
            imports="import os",
            read_stmts=f"print(sorted(os.listdir({repr(tmpdir)})))",
            mutate_stmts=(
                f"open(os.path.join({repr(tmpdir)}, 'new_file.txt'), 'w').close()"
            ),
            parse_value=lambda out: ast.literal_eval(
                out.strip().split("\n")[1].strip()
            ),
        )
        assert set(before).issubset(set(after)), (
            f"Expected {before} to be a subset of {after}"
        )
        assert "new_file.txt" in after, f"Expected 'new_file.txt' in {after}"


def test_stat():
    """Test that os.stat is deterministic.

    Changes the file's permissions and size, then verifies that stepping
    back and replaying os.stat produces the same metadata both times.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "test.txt")
        test_file.write_text("hello")
        _real_os.chmod(str(test_file), 0o644)
        fp = repr(str(test_file))

        verify_deterministic_mutated_value_util(
            imports="import os",
            read_stmts=[
                f"s = os.stat({fp})",
                "print((s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink))",
            ],
            mutate_stmts=[
                f"os.chmod({fp}, 0o755)",
                f"with open({fp}, 'a') as f: f.write(' world')",
            ],
        )


def test_stat_symlink():
    """Test that patched os.stat follows symlinks and is deterministic.

    Creates a real file and a symlink to it, then verifies that os.stat on
    the symlink returns the target file's metadata (follows the link),
    and that the result is deterministic on replay.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        target_file = Path(tmpdir, "target.txt")
        target_file.write_text("hello")
        _real_os.chmod(str(target_file), 0o644)

        symlink_path = Path(tmpdir, "link.txt")
        symlink_path.symlink_to(target_file)

        d = launch_dejaview(
            f"""
            import os
            st = os.stat({repr(str(symlink_path))})
            sf = os.stat({repr(str(target_file))})
            print((st.st_mode, st.st_size, st.st_uid, st.st_gid, st.st_nlink))
            print((sf.st_mode, sf.st_size, sf.st_uid, sf.st_gid, sf.st_nlink))
            os.chmod({repr(str(target_file))}, 0o755)
            with open({repr(str(target_file))}, 'a') as f: f.write(' world')
            st2 = os.stat({repr(str(symlink_path))})
            print((st2.st_mode, st2.st_size, st2.st_uid, st2.st_gid, st2.st_nlink))
            print()
            """
        )

        def get_printed_value(step_output: str) -> str:
            """Extract the printed value from pdb step output."""
            lines = step_output.strip().split("\n")
            return lines[1].strip()

        # 1. Advance to line 4 (first print: stat of symlink)
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)
        d.sendline("n")
        d.assert_line_number(4)

        # 2. Execute line 4: print stat of symlink -> stat_symlink
        d.sendline("n")
        step_out = d.assert_line_number(5)
        stat_symlink = get_printed_value(step_out)

        # 3. Execute line 5: print stat of target -> stat_target
        d.sendline("n")
        step_out = d.assert_line_number(6)
        stat_target = get_printed_value(step_out)

        # 4. Verify os.stat follows the symlink: symlink stat == target stat
        assert stat_symlink == stat_target, (
            f"os.stat should follow symlinks.\n"
            f"  symlink stat: {stat_symlink}\n"
            f"  target stat:  {stat_target}"
        )

        # 5. Execute lines 6-7 (chmod + write to target),
        #    line 8 (re-stat symlink), line 9 (print)
        d.sendline("n")
        d.assert_line_number(7)
        d.sendline("n")
        d.assert_line_number(8)
        d.sendline("n")
        d.assert_line_number(9)
        d.sendline("n")
        step_out = d.assert_line_number(10)
        stat_symlink_after = get_printed_value(step_out)

        # 6. Verify metadata changed (target was modified
        #    via chmod + write)
        assert stat_symlink != stat_symlink_after, (
            f"Expected symlink stat to reflect target "
            f"changes, but both are {stat_symlink}"
        )

        # 7. Step back to line 4 and replay to verify determinism
        d.sendline("back")
        d.assert_line_number(9)
        d.sendline("back")
        d.assert_line_number(8)
        d.sendline("back")
        d.assert_line_number(7)
        d.sendline("back")
        d.assert_line_number(6)
        d.sendline("back")
        d.assert_line_number(5)
        d.sendline("back")
        d.assert_line_number(4)

        # Re-execute line 4: stat of symlink
        d.sendline("n")
        step_out = d.assert_line_number(5)
        stat_symlink_replay = get_printed_value(step_out)
        assert stat_symlink == stat_symlink_replay, (
            f"symlink stat before mismatch: {stat_symlink} vs {stat_symlink_replay}"
        )

        # Re-execute line 5: stat of target
        d.sendline("n")
        step_out = d.assert_line_number(6)
        stat_target_replay = get_printed_value(step_out)
        assert stat_target == stat_target_replay, (
            f"target stat mismatch: {stat_target} vs {stat_target_replay}"
        )

        # Re-execute lines 6-9
        d.sendline("n")
        d.assert_line_number(7)
        d.sendline("n")
        d.assert_line_number(8)
        d.sendline("n")
        d.assert_line_number(9)
        d.sendline("n")
        step_out = d.assert_line_number(10)
        stat_symlink_after_replay = get_printed_value(step_out)
        assert stat_symlink_after == stat_symlink_after_replay, (
            f"symlink stat after mismatch: "
            f"{stat_symlink_after} vs {stat_symlink_after_replay}"
        )

        d.quit()


def test_lstat():
    """Test that os.lstat is deterministic.

    Changes permissions and size, then verifies that stepping back and
    replaying os.lstat produces the same metadata both times.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "test.txt")
        test_file.write_text("hello")
        _real_os.chmod(str(test_file), 0o644)
        fp = repr(str(test_file))

        verify_deterministic_mutated_value_util(
            imports="import os",
            read_stmts=[
                f"s = os.lstat({fp})",
                "print((s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink))",
            ],
            mutate_stmts=[
                f"os.chmod({fp}, 0o755)",
                f"with open({fp}, 'a') as f: f.write(' world')",
            ],
        )


def test_lstat_symlink():
    """Test that patched os.lstat does not follow symlinks and is deterministic.

    Creates a real file and a symlink, then verifies that os.lstat on the
    symlink returns metadata about the symlink itself (not the target),
    and that this is deterministic on replay.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        target_file = Path(tmpdir, "target.txt")
        target_file.write_text("hello")
        _real_os.chmod(str(target_file), 0o644)

        symlink_path = Path(tmpdir, "link.txt")
        symlink_path.symlink_to(target_file)

        d = launch_dejaview(
            f"""
            import os
            sl = os.lstat({repr(str(symlink_path))})
            tl = os.lstat({repr(str(target_file))})
            print((sl.st_mode, sl.st_size, sl.st_uid, sl.st_gid, sl.st_nlink))
            print((tl.st_mode, tl.st_size, tl.st_uid, tl.st_gid, tl.st_nlink))
            os.chmod({repr(str(target_file))}, 0o755)
            with open({repr(str(target_file))}, 'a') as f: f.write(' world')
            sl2 = os.lstat({repr(str(symlink_path))})
            print((sl2.st_mode, sl2.st_size, sl2.st_uid, sl2.st_gid, sl2.st_nlink))
            print()
            """
        )

        def get_printed_value(step_output: str) -> str:
            """Extract the printed value from pdb step output."""
            lines = step_output.strip().split("\n")
            return lines[1].strip()

        # 1. Advance to line 4 (first print: lstat of symlink)
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)
        d.sendline("n")
        d.assert_line_number(4)

        # 2. Execute line 4: print lstat of symlink -> lstat_symlink
        d.sendline("n")
        step_out = d.assert_line_number(5)
        lstat_symlink = get_printed_value(step_out)

        # 3. Execute line 5: print lstat of target -> lstat_target
        d.sendline("n")
        step_out = d.assert_line_number(6)
        lstat_target = get_printed_value(step_out)

        # 4. Verify os.lstat does NOT follow the symlink: they should differ
        #    (different st_mode since symlink has a different type, different st_ino)
        assert lstat_symlink != lstat_target, (
            f"os.lstat should NOT follow symlinks.\n"
            f"  symlink lstat: {lstat_symlink}\n"
            f"  target lstat:  {lstat_target}"
        )

        # 5. Execute lines 6-7 (chmod + write to target),
        #    line 8 (re-lstat symlink), line 9 (print)
        d.sendline("n")
        d.assert_line_number(7)
        d.sendline("n")
        d.assert_line_number(8)
        d.sendline("n")
        d.assert_line_number(9)
        d.sendline("n")
        step_out = d.assert_line_number(10)
        lstat_symlink_after = get_printed_value(step_out)

        # 6. The symlink's own metadata should be unchanged
        #    since we only modified the target
        assert lstat_symlink == lstat_symlink_after, (
            f"Symlink lstat should not change when target is modified.\n"
            f"  before: {lstat_symlink}\n"
            f"  after:  {lstat_symlink_after}"
        )

        # 7. Step back to line 4 and replay to verify determinism
        d.sendline("back")
        d.assert_line_number(9)
        d.sendline("back")
        d.assert_line_number(8)
        d.sendline("back")
        d.assert_line_number(7)
        d.sendline("back")
        d.assert_line_number(6)
        d.sendline("back")
        d.assert_line_number(5)
        d.sendline("back")
        d.assert_line_number(4)

        # Re-execute line 4: lstat of symlink
        d.sendline("n")
        step_out = d.assert_line_number(5)
        lstat_symlink_replay = get_printed_value(step_out)
        assert lstat_symlink == lstat_symlink_replay, (
            f"symlink lstat before mismatch: {lstat_symlink} vs {lstat_symlink_replay}"
        )

        # Re-execute line 5: lstat of target
        d.sendline("n")
        step_out = d.assert_line_number(6)
        lstat_target_replay = get_printed_value(step_out)
        assert lstat_target == lstat_target_replay, (
            f"target lstat mismatch: {lstat_target} vs {lstat_target_replay}"
        )

        # Re-execute lines 6-9
        d.sendline("n")
        d.assert_line_number(7)
        d.sendline("n")
        d.assert_line_number(8)
        d.sendline("n")
        d.assert_line_number(9)
        d.sendline("n")
        step_out = d.assert_line_number(10)
        lstat_symlink_after_replay = get_printed_value(step_out)
        assert lstat_symlink_after == lstat_symlink_after_replay, (
            f"symlink lstat after mismatch: "
            f"{lstat_symlink_after} vs {lstat_symlink_after_replay}"
        )

        d.quit()


def test_statvfs():
    """Test that os.statvfs is deterministic.

    Writes data to change disk usage, then verifies that stepping back
    and replaying os.statvfs produces the same result both times.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "test.txt")
        test_file.write_text("hello")
        fp = repr(str(test_file))

        verify_deterministic_mutated_value_util(
            imports="import os",
            read_stmts=[
                f"s = os.statvfs({fp})",
                "print(tuple(s))",
            ],
            mutate_stmts=f"with open({fp}, 'a') as f: f.write('x' * 10000)",
            # Free-block counts can be volatile on busy systems, so we don't
            # require the two reads to differ — the replay must still match.
            assert_changed=False,
        )


def test_statvfs_symlink():
    """Test that os.statvfs on a symlink is deterministic and follows the symlink.

    Creates a target file and a symlink, calls os.statvfs on both, verifies
    they return the same filesystem info, then steps back and replays.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        target_file = Path(tmpdir, "target.txt")
        target_file.write_text("hello")

        symlink_path = Path(tmpdir, "link.txt")
        symlink_path.symlink_to(target_file)

        d = launch_dejaview(
            f"""
            import os
            sl = os.statvfs({repr(str(symlink_path))})
            tl = os.statvfs({repr(str(target_file))})
            print(tuple(sl))
            print(tuple(tl))
            print()
            """
        )

        def get_printed_value(step_output: str) -> str:
            """Extract the printed value from pdb step output."""
            lines = step_output.strip().split("\n")
            return lines[1].strip()

        def stable_statvfs_fields(printed_tuple: str) -> tuple[int, ...]:
            """Extract only the stable structural fields from a printed statvfs tuple.

            statvfs indices: 0=f_bsize, 1=f_frsize, 2=f_blocks, 3=f_bfree,
            4=f_bavail, 5=f_files, 6=f_ffree, 7=f_favail, 8=f_flag, 9=f_namemax.

            Fields 3,4,6,7 (free block/inode counts) are volatile and can change
            between calls on a busy system, so we only compare the rest.
            """
            t = ast.literal_eval(printed_tuple)
            return tuple(t[i] for i in (0, 1, 2, 5, 8, 9))

        # 1. Advance to line 4
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)
        d.sendline("n")
        d.assert_line_number(4)

        # 2. Execute line 4: print statvfs of symlink
        d.sendline("n")
        step_out = d.assert_line_number(5)
        statvfs_symlink = get_printed_value(step_out)

        # 3. Execute line 5: print statvfs of target
        d.sendline("n")
        step_out = d.assert_line_number(6)
        statvfs_target = get_printed_value(step_out)

        # 4. Verify statvfs follows the symlink (same filesystem).
        #    Compare only stable structural fields because volatile counters
        #    (f_bfree, f_bavail, f_ffree, f_favail) can change between the
        #    two calls on a busy CI machine.
        assert stable_statvfs_fields(statvfs_symlink) == stable_statvfs_fields(
            statvfs_target
        ), (
            f"os.statvfs should return the same filesystem for symlink and target.\n"
            f"  symlink statvfs: {statvfs_symlink}\n"
            f"  target statvfs:  {statvfs_target}"
        )

        # 5. Step back to line 4 and replay to verify determinism
        d.sendline("back")
        d.assert_line_number(5)
        d.sendline("back")
        d.assert_line_number(4)

        # Re-execute line 4
        d.sendline("n")
        step_out = d.assert_line_number(5)
        statvfs_symlink_replay = get_printed_value(step_out)
        assert statvfs_symlink == statvfs_symlink_replay, (
            f"symlink statvfs mismatch: {statvfs_symlink} vs {statvfs_symlink_replay}"
        )

        # Re-execute line 5
        d.sendline("n")
        step_out = d.assert_line_number(6)
        statvfs_target_replay = get_printed_value(step_out)
        assert statvfs_target == statvfs_target_replay, (
            f"target statvfs mismatch: {statvfs_target} vs {statvfs_target_replay}"
        )

        d.quit()


def test_urandom():
    """Test that os.urandom is deterministic."""
    import_stmt = "import os"
    expr = "os.urandom(16).hex()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        expr=expr,
        compare=operator.ne,
    )


# ==============================================================================
# Side-effect functions
# ==============================================================================


def _get_printed_value(step_output: str) -> str:
    lines = step_output.strip().split("\n")
    return lines[1].strip()


def test_mkdir_replay():
    """Test that os.mkdir is a no-op on replay.

    Uses a custom launch_dejaview test because mkdir is not idempotent
    (calling it twice on the same path raises FileExistsError), so the
    generic verify_deterministic_memoized_value_util helper cannot be
    used.  Instead we call mkdir once, step back, and replay to verify
    the patched function returns the cached result without re-executing.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        target = str(Path(tmpdir, "new_dir"))

        d = launch_dejaview(
            f"""
            import os                                   # Line 1
            os.mkdir({repr(target)})                     # Line 2
            print(os.path.isdir({repr(target)}))         # Line 3
            print()                                     # Line 4
            """
        )

        # Execute through line 3
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)
        d.sendline("n")
        step_out = d.assert_line_number(4)
        value_play = _get_printed_value(step_out)

        # Step back to line 2 and replay
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("back")
        d.assert_line_number(2)

        d.sendline("n")
        d.assert_line_number(3)
        d.sendline("n")
        step_out = d.assert_line_number(4)
        value_replay = _get_printed_value(step_out)

        assert value_play == value_replay, (
            f"mkdir replay mismatch: {value_play!r} vs {value_replay!r}"
        )
        assert value_play == "True"
        d.quit()


# --- Environment mutation ---
def test_putenv_replay():
    key = f"DEJAVIEW_TEST_PUTENV_REPLAY_{_real_os.getpid()}_{id(object())}"
    value = "__dejaview_putenv_value__"

    had_original = key in _real_os.environ
    original_value = _real_os.environ.get(key)

    try:
        _real_os.unsetenv(key)
        _real_os.environ.pop(key, None)

        d = launch_dejaview(
            f"""
            import os
            import ctypes
            key = {repr(key)}
            value = {repr(value)}
            libc = ctypes.CDLL(None)
            libc.getenv.argtypes = [ctypes.c_char_p]
            libc.getenv.restype = ctypes.c_char_p
            print(libc.getenv(key.encode()) is not None)
            os.putenv(key, value)
            print(libc.getenv(key.encode()) is not None)
            print()
            """
        )

        try:
            d.assert_line_number(1)
            d.sendline("n")
            d.assert_line_number(2)
            d.sendline("n")
            d.assert_line_number(3)
            d.sendline("n")
            d.assert_line_number(4)
            d.sendline("n")
            d.assert_line_number(5)
            d.sendline("n")
            d.assert_line_number(6)
            d.sendline("n")
            d.assert_line_number(7)
            d.sendline("n")
            d.assert_line_number(8)
            d.sendline("n")
            first_play_out = d.assert_line_number(9)
            first_play = ast.literal_eval(_get_printed_value(first_play_out))
            d.sendline("n")
            d.assert_line_number(10)
            d.sendline("n")
            second_play_out = d.assert_line_number(11)
            second_play = ast.literal_eval(_get_printed_value(second_play_out))

            assert first_play is False
            assert second_play is True

            d.sendline("back")
            d.assert_line_number(10)
            d.sendline("back")
            d.assert_line_number(9)
            d.sendline("back")
            d.assert_line_number(8)

            d.sendline("n")
            first_replay_out = d.assert_line_number(9)
            first_replay = ast.literal_eval(_get_printed_value(first_replay_out))
            d.sendline("n")
            d.assert_line_number(10)
            d.sendline("n")
            second_replay_out = d.assert_line_number(11)
            second_replay = ast.literal_eval(_get_printed_value(second_replay_out))

            assert first_replay is False
            assert second_replay is True
        finally:
            d.quit()
    finally:
        if had_original:
            assert original_value is not None
            _real_os.putenv(key, original_value)
            _real_os.environ[key] = original_value
        else:
            _real_os.unsetenv(key)
            _real_os.environ.pop(key, None)


def test_unsetenv_replay():
    key = f"DEJAVIEW_TEST_UNSETENV_REPLAY_{_real_os.getpid()}_{id(object())}"
    seed = "__dejaview_unsetenv_seed__"

    had_original = key in _real_os.environ
    original_value = _real_os.environ.get(key)

    try:
        _real_os.putenv(key, seed)
        _real_os.environ[key] = seed

        d = launch_dejaview(
            f"""
            import os
            import ctypes
            key = {repr(key)}
            seed = {repr(seed)}
            libc = ctypes.CDLL(None)
            libc.getenv.argtypes = [ctypes.c_char_p]
            libc.getenv.restype = ctypes.c_char_p
            os.putenv(key, seed)
            print(libc.getenv(key.encode()) is not None)
            os.unsetenv(key)
            print(libc.getenv(key.encode()) is not None)
            print()
            """
        )

        try:
            d.assert_line_number(1)
            d.sendline("n")
            d.assert_line_number(2)
            d.sendline("n")
            d.assert_line_number(3)
            d.sendline("n")
            d.assert_line_number(4)
            d.sendline("n")
            d.assert_line_number(5)
            d.sendline("n")
            d.assert_line_number(6)
            d.sendline("n")
            d.assert_line_number(7)
            d.sendline("n")
            d.assert_line_number(8)
            d.sendline("n")
            d.assert_line_number(9)
            d.sendline("n")
            first_play_out = d.assert_line_number(10)
            first_play = ast.literal_eval(_get_printed_value(first_play_out))
            d.sendline("n")
            d.assert_line_number(11)
            d.sendline("n")
            second_play_out = d.assert_line_number(12)
            second_play = ast.literal_eval(_get_printed_value(second_play_out))

            assert first_play is True
            assert second_play is False

            d.sendline("back")
            d.assert_line_number(11)
            d.sendline("back")
            d.assert_line_number(10)
            d.sendline("back")
            d.assert_line_number(9)

            d.sendline("n")
            first_replay_out = d.assert_line_number(10)
            first_replay = ast.literal_eval(_get_printed_value(first_replay_out))
            d.sendline("n")
            d.assert_line_number(11)
            d.sendline("n")
            second_replay_out = d.assert_line_number(12)
            second_replay = ast.literal_eval(_get_printed_value(second_replay_out))

            assert first_replay is True
            assert second_replay is False
        finally:
            d.quit()
    finally:
        if had_original:
            assert original_value is not None
            _real_os.putenv(key, original_value)
            _real_os.environ[key] = original_value
        else:
            _real_os.unsetenv(key)
            _real_os.environ.pop(key, None)


# ==============================================================================
# Iterator-returning functions
# ==============================================================================


def test_walk():
    """Test that os.walk is deterministic on replay.

    os.walk is intentionally not patched directly.
    It remains deterministic because it delegates to patched low-level
    functions (notably os.scandir).

    Uses forward-only determinism testing because iterator-based patchers
    create intermediate frame events that prevent back-stepping from
    aligning with ``n``-step boundaries.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "sub1").mkdir()
        Path(tmpdir, "sub2").mkdir()
        Path(tmpdir, "sub1", "a.txt").touch()
        Path(tmpdir, "sub2", "b.txt").touch()
        Path(tmpdir, "root.txt").touch()

        script = f"""
            import os
            print(str(list(os.walk({repr(tmpdir)}))))
            print(str(list(os.walk({repr(tmpdir)}))))
        """
        PropertyTester.test_determinism_property(
            program=script,
            command_sequence=[DebugCommand.STEP] * 2,
            num_runs=3,
        )


def test_scandir():
    """Test that os.scandir is deterministic on replay.

    os.scandir is patched with ScanDirPatcher, which records consumed
    entries during play and replays them via a scandir-like iterator.

    Uses forward-only determinism testing because iterator-based patchers
    create intermediate frame events that prevent back-stepping from
    aligning with ``n``-step boundaries.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "a.txt").touch()
        Path(tmpdir, "b.txt").touch()
        Path(tmpdir, "c.txt").touch()

        script = f"""
            import os
            print(sorted(e.name for e in os.scandir({repr(tmpdir)})))
            print(sorted(e.name for e in os.scandir({repr(tmpdir)})))
        """
        PropertyTester.test_determinism_property(
            program=script,
            command_sequence=[DebugCommand.STEP] * 2,
            num_runs=3,
        )


def test_scandir_delete_file():
    """Test that os.scandir reflects a deleted file and replays deterministically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "a.txt").touch()
        Path(tmpdir, "b.txt").touch()
        Path(tmpdir, "c.txt").touch()
        deleted_file = Path(tmpdir, "b.txt")

        d = launch_dejaview(
            f"""
            import os
            print(sorted(e.name for e in os.scandir({repr(tmpdir)})))
            os.remove({repr(str(deleted_file))})
            print(sorted(e.name for e in os.scandir({repr(tmpdir)})))
            print()
            """
        )

        def parse_printed_names(step_output: str) -> list[str]:
            for raw_line in step_output.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = ast.literal_eval(line)
                except (SyntaxError, ValueError):
                    continue
                if isinstance(value, list) and all(
                    isinstance(name, str) for name in value
                ):
                    return value
            raise AssertionError(
                f"No printed filename list found in output:\n{step_output}"
            )

        expected_before = ["a.txt", "b.txt", "c.txt"]
        expected_after = ["a.txt", "c.txt"]

        # Play + replay around first scandir print (before deletion).
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        before_play_out = d.assert_line_number(3)
        names_before_play = parse_printed_names(before_play_out)

        d.sendline("back")
        d.assert_line_number(2)
        d.sendline("n")
        before_replay_out = d.assert_line_number(3)
        names_before_replay = parse_printed_names(before_replay_out)

        assert names_before_play == expected_before, (
            f"Expected first scandir output {expected_before!r}, "
            f"got {names_before_play!r}"
        )
        assert names_before_replay == expected_before, (
            f"Expected replay first scandir output {expected_before!r}, "
            f"got {names_before_replay!r}"
        )

        # Play + replay around second scandir print (after deletion).
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        after_play_out = d.assert_line_number(5)
        names_after_play = parse_printed_names(after_play_out)
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("n")
        after_replay_out = d.assert_line_number(5)
        names_after_replay = parse_printed_names(after_replay_out)

        assert names_after_play == expected_after, (
            f"Expected second scandir output {expected_after!r}, "
            f"got {names_after_play!r}"
        )
        assert names_after_replay == expected_after, (
            f"Expected replay second scandir output {expected_after!r}, "
            f"got {names_after_replay!r}"
        )
        d.quit()


def test_scandir_add_file():
    """Test that os.scandir reflects an added file and replays deterministically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "a.txt").touch()
        Path(tmpdir, "b.txt").touch()
        Path(tmpdir, "c.txt").touch()
        new_file = Path(tmpdir, "new_file.txt")

        d = launch_dejaview(
            f"""
            import os
            print(sorted(e.name for e in os.scandir({repr(tmpdir)})))
            os.open({repr(str(new_file))}, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            print(sorted(e.name for e in os.scandir({repr(tmpdir)})))
            print()
            """
        )

        def parse_printed_names(step_output: str) -> list[str]:
            for raw_line in step_output.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = ast.literal_eval(line)
                except (SyntaxError, ValueError):
                    continue
                if isinstance(value, list) and all(
                    isinstance(name, str) for name in value
                ):
                    return value
            raise AssertionError(
                f"No printed filename list found in output:\n{step_output}"
            )

        expected_before = ["a.txt", "b.txt", "c.txt"]
        expected_after = ["a.txt", "b.txt", "c.txt", "new_file.txt"]

        # Play + replay around first scandir print (before creation).
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        before_play_out = d.assert_line_number(3)
        names_before_play = parse_printed_names(before_play_out)

        d.sendline("back")
        d.assert_line_number(2)
        d.sendline("n")
        before_replay_out = d.assert_line_number(3)
        names_before_replay = parse_printed_names(before_replay_out)

        assert names_before_play == expected_before, (
            f"Expected first scandir output {expected_before!r}, "
            f"got {names_before_play!r}"
        )
        assert names_before_replay == expected_before, (
            f"Expected replay first scandir output {expected_before!r}, "
            f"got {names_before_replay!r}"
        )

        # Play + replay around second scandir print (after creation).
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        after_play_out = d.assert_line_number(5)
        names_after_play = parse_printed_names(after_play_out)
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("n")
        after_replay_out = d.assert_line_number(5)
        names_after_replay = parse_printed_names(after_replay_out)

        assert names_after_play == expected_after, (
            f"Expected second scandir output {expected_after!r}, "
            f"got {names_after_play!r}"
        )
        assert names_after_replay == expected_after, (
            f"Expected replay second scandir output {expected_after!r}, "
            f"got {names_after_replay!r}"
        )
        d.quit()


# ==============================================================================
# Low-level I/O
# ==============================================================================


def test_os_open_read():
    """Test that os.open and os.read are deterministic on replay."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "io_test.txt")
        test_file.write_text("hello world")

        d = launch_dejaview(
            f"""
            import os
            print(os.read(os.open({repr(str(test_file))}, os.O_RDONLY), 100))
            print(os.read(os.open({repr(str(test_file))}, os.O_RDONLY), 100))
            print()
            """
        )

        def parse_printed_value(step_output: str) -> bytes:
            for raw_line in step_output.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = ast.literal_eval(line)
                except (SyntaxError, ValueError):
                    continue
                if isinstance(value, bytes):
                    return value
            raise AssertionError(
                f"No printed bytes value found in output:\n{step_output}"
            )

        # Play first read.
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        first_play_out = d.assert_line_number(3)
        value_first_play = parse_printed_value(first_play_out)

        # Replay first read from line 2.
        d.sendline("back")
        d.assert_line_number(2)
        d.sendline("n")
        first_replay_out = d.assert_line_number(3)
        value_first_replay = parse_printed_value(first_replay_out)

        # Execute second read forward.
        d.sendline("n")
        second_play_out = d.assert_line_number(4)
        value_second_play = parse_printed_value(second_play_out)

        assert value_first_play == b"hello world"
        assert value_second_play == b"hello world"
        assert value_first_play == value_first_replay, (
            f"first read replay mismatch: {value_first_play!r} vs "
            f"{value_first_replay!r}"
        )
        d.quit()


def test_os_write():
    """Test that os.write produces deterministic forward outputs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "write_test.txt")

        d = launch_dejaview(
            f"""
            import os
            print(os.write(os.open({repr(str(test_file))},\
                  os.O_WRONLY | os.O_CREAT), b'hello'))
            print(os.write(os.open({repr(str(test_file))},\
                  os.O_WRONLY | os.O_CREAT), b'hello'))
            print()
            """
        )

        def parse_printed_value(step_output: str) -> int:
            for raw_line in step_output.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = ast.literal_eval(line)
                except (SyntaxError, ValueError):
                    continue
                if isinstance(value, int):
                    return value
            raise AssertionError(
                f"No printed integer value found in output:\n{step_output}"
            )

        # Execute first and second writes forward and verify stable output.
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        first_play_out = d.assert_line_number(3)
        value_first_play = parse_printed_value(first_play_out)

        # Execute second write forward.
        d.sendline("n")
        second_play_out = d.assert_line_number(4)
        value_second_play = parse_printed_value(second_play_out)

        assert value_first_play == 5
        assert value_second_play == 5
        assert value_first_play == value_second_play, (
            f"forward write mismatch: {value_first_play!r} vs {value_second_play!r}"
        )
        d.quit()


def test_os_read_write():
    fd, temp_path = tempfile.mkstemp(prefix="dejaview-os-read-write-", suffix=".txt")
    try:
        _real_os.write(fd, b"hello")
        _real_os.close(fd)

        d = launch_dejaview(
            f"""
            import os
            fd = os.open({repr(temp_path)}, os.O_RDWR)
            print(os.read(fd, 5))
            os.lseek(fd, 0, os.SEEK_SET)
            print(os.write(fd, b'HELLO'))
            os.close(fd)
            fd2 = os.open({repr(temp_path)}, os.O_RDONLY)
            print(os.read(fd2, 5))
            os.close(fd2)
            print()
            """
        )

        def parse_printed_value(step_output: str, expected_type: type) -> bytes | int:
            for raw_line in step_output.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = ast.literal_eval(line)
                except (SyntaxError, ValueError):
                    continue
                if isinstance(value, expected_type):
                    return type_cast(bytes | int, value)
            raise AssertionError(
                f"No printed value of type {expected_type.__name__} found in output:\n"
                f"{step_output}"
            )

        def run_forward_to_end() -> list[bytes | int]:
            values: list[bytes | int] = []
            expected_progression = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10]
            for expected_line in expected_progression:
                d.sendline("n")
                step_out = d.assert_line_number(expected_line)
                for expected_type in (bytes, int):
                    try:
                        value = parse_printed_value(step_out, expected_type)
                    except AssertionError:
                        continue
                    values.append(value)
                    break
            return values

        def rewind_to_line_1() -> None:
            rewind_progression = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
            for expected_line in rewind_progression:
                d.sendline("back")
                d.assert_line_number(expected_line)

        d.assert_line_number(1)
        forward_values = run_forward_to_end()
        rewind_to_line_1()
        replay_values = run_forward_to_end()

        expected_values: list[bytes | int] = [b"hello", 5, b"HELLO"]
        assert forward_values == expected_values, (
            f"forward values mismatch: expected {expected_values!r},"
            f"got {forward_values!r}"
        )
        assert replay_values == expected_values, (
            f"replay values mismatch: expected {expected_values!r},"
            f" got {replay_values!r}"
        )
        assert forward_values == replay_values, (
            f"forward/replay mismatch: {forward_values!r} vs {replay_values!r}"
        )

        d.quit()
    finally:
        Path(temp_path).unlink(missing_ok=True)
