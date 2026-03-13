import ast
import operator
import os as _real_os
import tempfile
from pathlib import Path

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


def test_getppid():
    """Test that os.getppid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getppid()",
        compare=operator.eq,
    )


def test_getuid():
    """Test that os.getuid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getuid()",
        compare=operator.eq,
    )


def test_getgid():
    """Test that os.getgid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getgid()",
        compare=operator.eq,
    )


def test_geteuid():
    """Test that os.geteuid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.geteuid()",
        compare=operator.eq,
    )


def test_getegid():
    """Test that os.getegid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getegid()",
        compare=operator.eq,
    )


# The unpatched version of `os.getlogin()` fails in WSL so I am
# commenting this test out.
# def test_getlogin():
#     """Test that os.getlogin is deterministic."""
#     verify_deterministic_memoized_value_util(
#         imports="import os",
#         expr="os.getlogin()",
#     )


def test_getpgid():
    """Test that os.getpgid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getpgid(os.getpid())",
        compare=operator.eq,
    )


def test_getpgrp():
    """Test that os.getpgrp is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getpgrp()",
        compare=operator.eq,
    )


def test_getpriority():
    """Test that os.getpriority is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getpriority(os.PRIO_PROCESS, 0)",
        compare=operator.eq,
    )


def test_getresgid():
    """Test that os.getresgid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getresgid()",
        compare=operator.eq,
    )


def test_getresuid():
    """Test that os.getresuid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getresuid()",
        compare=operator.eq,
    )


def test_getsid():
    """Test that os.getsid is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getsid(0)",
        compare=operator.eq,
    )


def test_getgroups():
    """Test that os.getgroups is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getgroups()",
        compare=operator.eq,
    )


def test_getgrouplist():
    """Test that os.getgrouplist is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os, pwd",
        expr="os.getgrouplist(pwd.getpwuid(os.getuid()).pw_name, os.getgid())",
        compare=operator.eq,
    )

# ==============================================================================
# System information
# ==============================================================================


def test_times():
    """Test that os.times is deterministic."""
    import_stmt = "import os"
    expr = "os.times()"

    verify_deterministic_memoized_value_util(imports=import_stmt, expr=expr)


def test_uname():
    """Test that os.uname is deterministic."""
    import_stmt = "import os"
    expr = "os.uname()"

    verify_deterministic_memoized_value_util(imports=import_stmt, expr=expr)


def test_cpu_count():
    """Test that os.cpu_count is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.cpu_count()",
        compare=operator.eq,
    )


def test_getloadavg():
    """Test that os.getloadavg is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getloadavg()",
    )


def test_confstr():
    """Test that os.confstr is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.confstr('CS_PATH')",
        compare=operator.eq,
    )


def test_sysconf():
    """Test that os.sysconf is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.sysconf('SC_PAGE_SIZE')",
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


def test_fstat():
    """Test that os.fstat is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "fstat_test.txt")
        test_file.write_text("hello")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=f"os.fstat(os.open({repr(str(test_file))}, os.O_RDONLY))",
        )


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


def test_fstatvfs():
    """Test that os.fstatvfs is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "fstatvfs_test.txt")
        test_file.write_text("hello")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=f"tuple(os.fstatvfs(os.open({repr(str(test_file))}, os.O_RDONLY)))",
        )


def test_readlink():
    """Test that os.readlink is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir, "target.txt")
        target.write_text("hello")
        link = Path(tmpdir, "link.txt")
        link.symlink_to(target)

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=f"os.readlink({repr(str(link))})",
            compare=operator.eq,
        )


def test_access():
    """Test that os.access is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "access_test.txt")
        test_file.write_text("hello")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=f"os.access({repr(str(test_file))}, os.R_OK)",
            compare=operator.eq,
        )


def test_fpathconf():
    """Test that os.fpathconf is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "fpathconf_test.txt")
        test_file.write_text("hello")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=(
                f"os.fpathconf(os.open({repr(str(test_file))},"
                f" os.O_RDONLY), 'PC_NAME_MAX')"
            ),
            compare=operator.eq,
        )


def test_pathconf():
    """Test that os.pathconf is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "pathconf_test.txt")
        test_file.write_text("hello")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=f"os.pathconf({repr(str(test_file))}, 'PC_NAME_MAX')",
            compare=operator.eq,
        )


# ==============================================================================
# Working directory
# ==============================================================================


def test_getcwd():
    """Test that os.chdir updates the cwd but stepping back restores
    state effectively for getcwd."""
    with tempfile.TemporaryDirectory() as tmpdir:
        verify_deterministic_mutated_value_util(
            imports="import os",
            read_stmts="print(os.getcwd())",
            mutate_stmts=f"os.chdir({repr(tmpdir)})",
        )


def test_getcwdb():
    """Test that os.getcwdb is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.getcwdb()",
        compare=operator.eq,
    )


# ==============================================================================
# Terminal / device
# ==============================================================================

# TODO: look into why running `uv run pytest` makes
# `dejaview/tests/test_patch.py::test_get_terminal_size` fail
# def test_get_terminal_size():
#     """Test that os.get_terminal_size is deterministic."""
#     verify_deterministic_memoized_value_util(
#         imports="import os",
#         expr="tuple(os.get_terminal_size())",
#         compare=operator.eq,
#     )


# def test_isatty():
#     """Test that os.isatty is deterministic."""
#     verify_deterministic_memoized_value_util(
#         imports="import os",
#         expr="os.isatty(0)",
#         compare=operator.eq,
#     )


# def test_ctermid():
#     """Test that os.ctermid is deterministic."""
#     verify_deterministic_memoized_value_util(
#         imports="import os",
#         expr="os.ctermid()",
#         compare=operator.eq,
#     )


# ==============================================================================
# File-descriptor state
# ==============================================================================


def test_get_blocking():
    """Test that os.get_blocking is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.get_blocking(0)",
        compare=operator.eq,
    )


def test_get_inheritable():
    """Test that os.get_inheritable is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.get_inheritable(0)",
        compare=operator.eq,
    )


# ==============================================================================
# Other queries
# ==============================================================================


def test_get_exec_path():
    """Test that os.get_exec_path is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.get_exec_path()",
        compare=operator.eq,
    )


def test_urandom():
    """Test that os.urandom is deterministic."""
    import_stmt = "import os"
    expr = "os.urandom(16).hex()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        expr=expr,
    )


# ==============================================================================
# Scheduling queries
# ==============================================================================


def test_sched_getaffinity():
    """Test that os.sched_getaffinity is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="sorted(os.sched_getaffinity(0))",
        compare=operator.eq,
    )


def test_sched_get_priority_max():
    """Test that os.sched_get_priority_max is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.sched_get_priority_max(os.SCHED_OTHER)",
        compare=operator.eq,
    )


def test_sched_get_priority_min():
    """Test that os.sched_get_priority_min is deterministic."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.sched_get_priority_min(os.SCHED_OTHER)",
        compare=operator.eq,
    )


# ==============================================================================
# Side-effect functions
# ==============================================================================

# --- File permissions / ownership ---


def test_chmod_replay():
    """Test that os.chmod is deterministic (no-op on replay).

    chmod is idempotent so calling it twice in the test script
    is safe.  The key property is that on replay the cached None
    is returned without re-executing the syscall.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "chmod_test.txt")
        test_file.write_text("hello")
        _real_os.chmod(str(test_file), 0o644)

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=f"os.chmod({repr(str(test_file))}, 0o755)",
        )


# --- Create / remove ---


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

        def get_printed_value(step_output: str) -> str:
            lines = step_output.strip().split("\n")
            return lines[1].strip()

        # Execute through line 3
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)
        d.sendline("n")
        step_out = d.assert_line_number(4)
        value_play = get_printed_value(step_out)

        # Step back to line 2 and replay
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("back")
        d.assert_line_number(2)

        d.sendline("n")
        d.assert_line_number(3)
        d.sendline("n")
        step_out = d.assert_line_number(4)
        value_replay = get_printed_value(step_out)

        assert value_play == value_replay, (
            f"mkdir replay mismatch: {value_play!r} vs {value_replay!r}"
        )
        assert value_play == "True"
        d.quit()


# --- Truncation ---


def test_truncate_replay():
    """Test that os.truncate is deterministic (no-op on replay)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "truncate_test.txt")
        test_file.write_text("hello world")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=f"os.truncate({repr(str(test_file))}, 5)",
        )


# --- Subprocess ---


def test_system_replay():
    """Test that os.system is deterministic (no-op on replay)."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.system('true')",
        compare=operator.eq,
    )


# ==============================================================================
# Iterator-returning functions
# ==============================================================================


def test_walk():
    """Test that os.walk is deterministic on replay.

    os.walk is patched with IteratorPatcher which eagerly consumes
    the generator during play and returns a fresh iterator during replay.

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

    os.scandir is patched with ScanDirPatcher which eagerly consumes
    the iterator during play and returns a fresh _ReplayableIterator
    that also supports context-manager usage.

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


# ==============================================================================
# Low-level I/O
# ==============================================================================


def test_os_open_read():
    """Test that os.open and os.read are deterministic on replay."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "io_test.txt")
        test_file.write_text("hello world")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=f"os.read(os.open({repr(str(test_file))}, os.O_RDONLY), 100)",
        )


def test_os_write():
    """Test that os.write is deterministic on replay."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "write_test.txt")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=(
                f"os.write("
                f"os.open({repr(str(test_file))}, os.O_WRONLY | os.O_CREAT), "
                f"b'hello')"
            ),
            compare=operator.eq,
        )


def test_os_pipe():
    """Test that os.pipe is deterministic on replay."""
    verify_deterministic_memoized_value_util(
        imports="import os",
        expr="os.pipe()",
    )


def test_os_dup():
    """Test that os.dup is deterministic on replay."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "dup_test.txt")
        test_file.write_text("hello")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=f"os.dup(os.open({repr(str(test_file))}, os.O_RDONLY))",
        )


def test_os_lseek():
    """Test that os.lseek is deterministic on replay."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "lseek_test.txt")
        test_file.write_text("hello world")

        verify_deterministic_memoized_value_util(
            imports="import os",
            expr=(
                f"os.lseek("
                f"os.open({repr(str(test_file))}, os.O_RDONLY), "
                f"5, os.SEEK_SET)"
            ),
            compare=operator.eq,
        )
