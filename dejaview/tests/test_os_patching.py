import ast
import operator
import os as _real_os
import tempfile
from pathlib import Path

from dejaview.tests.util import (
    launch_dejaview,
    verify_deterministic_memoized_value_util,
)


def test_getpid():
    """Test that os.getpid is deterministic."""
    import_stmt = "import os"
    expr = "os.getpid()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        expr=expr,
        compare=operator.eq,
    )


def test_getppid():
    """Test that os.getppid is deterministic."""
    import_stmt = "import os"
    expr = "os.getppid()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        expr=expr,
        compare=operator.eq,
    )


def test_getuid():
    """Test that os.getuid is deterministic."""
    import_stmt = "import os"
    expr = "os.getuid()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        expr=expr,
        compare=operator.eq,
    )


def test_getgid():
    """Test that os.getgid is deterministic."""
    import_stmt = "import os"
    expr = "os.getgid()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        expr=expr,
        compare=operator.eq,
    )


def test_geteuid():
    """Test that os.geteuid is deterministic."""
    import_stmt = "import os"
    expr = "os.geteuid()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        expr=expr,
        compare=operator.eq,
    )


def test_getegid():
    """Test that os.getegid is deterministic."""
    import_stmt = "import os"
    expr = "os.getegid()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        expr=expr,
        compare=operator.eq,
    )


def test_getenv():
    """Test that os.getenv is deterministic.

    idea is to use getenv to find the environmental variables
    then change the environment variable and find the environment
    variable again to see if it changed
    """
    env_var = "_DEJAVIEW_TEST_GETENV"
    original_value = "hello_dejaview"

    # Set the env var so the subprocess inherits it, and save any pre-existing value
    old_value = _real_os.environ.get(env_var)
    _real_os.environ[env_var] = original_value

    try:
        d = launch_dejaview(
            f"""
            import os                                      # Line 1
            print(os.getenv({repr(env_var)}))              # Line 2
            os.environ[{repr(env_var)}] = "changed_value"  # Line 3
            print(os.getenv({repr(env_var)}))              # Line 4
            print()                                        # Line 5
            """
        )

        def get_printed_value(step_output: str) -> str:
            """Extract the printed value from pdb step output.

            The printed output appears on the second line (index 1) of the step output.
            """
            lines = step_output.strip().split("\n")
            return lines[1].strip()

        # 1. Advance to line 2
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)

        # 2. Execute line 2: print(os.getenv(...)) -> value_before
        d.sendline("n")
        step_out = d.assert_line_number(3)
        value_before = get_printed_value(step_out)

        # 3. Execute line 3 (change env var), then line 4 -> value_after
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        step_out = d.assert_line_number(5)
        value_after = get_printed_value(step_out)

        # 4. Verify the env var was read correctly
        assert value_before == original_value, (
            f"Expected {original_value!r}, got {value_before!r}"
        )
        assert value_after == "changed_value", (
            f"Expected 'changed_value', got {value_after!r}"
        )

        # 5. Step back to line 2 and replay to verify determinism
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("back")
        d.assert_line_number(2)

        # Re-execute line 2
        d.sendline("n")
        step_out = d.assert_line_number(3)
        value_before_replay = get_printed_value(step_out)
        assert value_before == value_before_replay, (
            f"getenv before mismatch: {value_before!r} vs {value_before_replay!r}"
        )

        # Re-execute line 3 (change env var), then line 4
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        step_out = d.assert_line_number(5)
        value_after_replay = get_printed_value(step_out)
        assert value_after == value_after_replay, (
            f"getenv after mismatch: {value_after!r} vs {value_after_replay!r}"
        )

        d.quit()

    finally:
        # Restore the environment variable to its original state
        if old_value is None:
            _real_os.environ.pop(env_var, None)
        else:
            _real_os.environ[env_var] = old_value


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


def test_listdir():
    """Test that os.listdir is deterministic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some initial files in tmpdir
        Path(tmpdir, "a.txt").touch()
        Path(tmpdir, "b.txt").touch()

        d = launch_dejaview(
            f"""
            import os                                                        # Line 1
            print(sorted(os.listdir({repr(tmpdir)})))                        # Line 2
            open(os.path.join({repr(tmpdir)}, 'new_file.txt'), 'w').close()  # Line 3
            print(sorted(os.listdir({repr(tmpdir)})))                        # Line 4
            print()                                                          # Line 5
            """
        )

        def get_printed_listing(step_output: str) -> list[str]:
            """Extract the printed listing from pdb step output.

            The printed output is a Python list repr like "['a.txt', 'b.txt']".
            It appears on the second line (index 1) of the step output.
            """
            lines = step_output.strip().split("\n")
            return ast.literal_eval(lines[1].strip())

        # 1. Advance to line 2
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)

        # 2. Execute line 2: print(sorted(os.listdir(...))) -> listing_before
        d.sendline("n")
        step_out = d.assert_line_number(3)
        listing_before = get_printed_listing(step_out)

        # 3. Execute line 3 (create file), then line 4 -> listing_after
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        step_out = d.assert_line_number(5)
        listing_after = get_printed_listing(step_out)

        # 4. Go back to line 2 and re-execute lines 2-4 to verify
        #    the same output and order is reproduced on replay
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("back")
        d.assert_line_number(2)

        # Re-execute line 2
        d.sendline("n")
        step_out = d.assert_line_number(3)
        listing_before_replay = get_printed_listing(step_out)
        assert listing_before == listing_before_replay, (
            f"Listing before mismatch: {listing_before} vs {listing_before_replay}"
        )

        # Re-execute line 3 (create file), then line 4
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        step_out = d.assert_line_number(5)
        listing_after_replay = get_printed_listing(step_out)
        assert listing_after == listing_after_replay, (
            f"Listing after mismatch: {listing_after} vs {listing_after_replay}"
        )

        # 5. Verify that files from line 4 are a superset of files from line 2
        #    (since we added a file in line 3)
        assert set(listing_before).issubset(set(listing_after)), (
            f"Expected {listing_before} to be a subset of {listing_after}"
        )
        assert "new_file.txt" in listing_after, (
            f"Expected 'new_file.txt' in {listing_after}"
        )

        d.quit()


def test_stat():
    """Test that os.stat is deterministic.

    idea is to change the stat of the file and see if it changes on replay
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "test.txt")
        test_file.write_text("hello")
        # Set initial permissions to a known state
        _real_os.chmod(str(test_file), 0o644)

        d = launch_dejaview(
            f"""
            import os                                                       # Line 1
            s = os.stat({repr(str(test_file))})                             # Line 2
            print((s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink))   # Line 3
            os.chmod({repr(str(test_file))}, 0o755)                         # Line 4
            with open({repr(str(test_file))}, 'a') as f: f.write(' world')  # Line 5
            s = os.stat({repr(str(test_file))})                             # Line 6
            print((s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink))   # Line 7
            print()                                                         # Line 8
            """
        )

        def get_printed_value(step_output: str) -> str:
            """Extract the printed value from pdb step output.

            The printed output appears on the second line (index 1) of the step output.
            """
            lines = step_output.strip().split("\n")
            return lines[1].strip()

        # 1. Advance to line 3 (first print of stat metadata)
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)

        # 2. Execute line 3: print stat metadata -> stat_before
        d.sendline("n")
        step_out = d.assert_line_number(4)
        stat_before = get_printed_value(step_out)

        # 3. Execute lines 4-5 (chmod + write), line 6
        #    (re-stat), line 7 (print) -> stat_after
        d.sendline("n")
        d.assert_line_number(5)
        d.sendline("n")
        d.assert_line_number(6)
        d.sendline("n")
        d.assert_line_number(7)
        d.sendline("n")
        step_out = d.assert_line_number(8)
        stat_after = get_printed_value(step_out)

        # 4. Verify the metadata actually changed (mode and size differ)
        assert stat_before != stat_after, (
            f"Expected stat metadata to change, but both are {stat_before}"
        )

        # 5. Step back to line 3 and replay to verify determinism
        d.sendline("back")
        d.assert_line_number(7)
        d.sendline("back")
        d.assert_line_number(6)
        d.sendline("back")
        d.assert_line_number(5)
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("back")
        d.assert_line_number(3)

        # Re-execute line 3
        d.sendline("n")
        step_out = d.assert_line_number(4)
        stat_before_replay = get_printed_value(step_out)
        assert stat_before == stat_before_replay, (
            f"stat before mismatch: {stat_before} vs {stat_before_replay}"
        )

        # Re-execute lines 4-7
        d.sendline("n")
        d.assert_line_number(5)
        d.sendline("n")
        d.assert_line_number(6)
        d.sendline("n")
        d.assert_line_number(7)
        d.sendline("n")
        step_out = d.assert_line_number(8)
        stat_after_replay = get_printed_value(step_out)
        assert stat_after == stat_after_replay, (
            f"stat after mismatch: {stat_after} vs {stat_after_replay}"
        )

        d.quit()


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

    Creates a temporary file, lstats it, changes permissions and size,
    lstats again, then steps back and replays to verify determinism.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "test.txt")
        test_file.write_text("hello")
        _real_os.chmod(str(test_file), 0o644)

        d = launch_dejaview(
            f"""
            import os                                                       # Line 1
            s = os.lstat({repr(str(test_file))})                            # Line 2
            print((s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink))   # Line 3
            os.chmod({repr(str(test_file))}, 0o755)                         # Line 4
            with open({repr(str(test_file))}, 'a') as f: f.write(' world')  # Line 5
            s = os.lstat({repr(str(test_file))})                            # Line 6
            print((s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink))   # Line 7
            print()                                                         # Line 8
            """
        )

        def get_printed_value(step_output: str) -> str:
            """Extract the printed value from pdb step output."""
            lines = step_output.strip().split("\n")
            return lines[1].strip()

        # 1. Advance to line 3 (first print of lstat metadata)
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)

        # 2. Execute line 3: print lstat metadata -> lstat_before
        d.sendline("n")
        step_out = d.assert_line_number(4)
        lstat_before = get_printed_value(step_out)

        # 3. Execute lines 4-5 (chmod + write), line 6 (re-lstat), line 7 (print)
        d.sendline("n")
        d.assert_line_number(5)
        d.sendline("n")
        d.assert_line_number(6)
        d.sendline("n")
        d.assert_line_number(7)
        d.sendline("n")
        step_out = d.assert_line_number(8)
        lstat_after = get_printed_value(step_out)

        # 4. Verify the metadata actually changed
        assert lstat_before != lstat_after, (
            f"Expected lstat metadata to change, but both are {lstat_before}"
        )

        # 5. Step back to line 3 and replay to verify determinism
        d.sendline("back")
        d.assert_line_number(7)
        d.sendline("back")
        d.assert_line_number(6)
        d.sendline("back")
        d.assert_line_number(5)
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("back")
        d.assert_line_number(3)

        # Re-execute line 3
        d.sendline("n")
        step_out = d.assert_line_number(4)
        lstat_before_replay = get_printed_value(step_out)
        assert lstat_before == lstat_before_replay, (
            f"lstat before mismatch: {lstat_before} vs {lstat_before_replay}"
        )

        # Re-execute lines 4-7
        d.sendline("n")
        d.assert_line_number(5)
        d.sendline("n")
        d.assert_line_number(6)
        d.sendline("n")
        d.assert_line_number(7)
        d.sendline("n")
        step_out = d.assert_line_number(8)
        lstat_after_replay = get_printed_value(step_out)
        assert lstat_after == lstat_after_replay, (
            f"lstat after mismatch: {lstat_after} vs {lstat_after_replay}"
        )

        d.quit()


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

    Creates a temporary file, calls os.statvfs on it, writes more data to
    change disk usage, calls statvfs again, then steps back and replays
    to verify determinism.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir, "test.txt")
        test_file.write_text("hello")

        d = launch_dejaview(
            f"""
            import os
            s = os.statvfs({repr(str(test_file))})
            print(tuple(s))
            with open({repr(str(test_file))}, 'a') as f: f.write('x' * 10000)
            s2 = os.statvfs({repr(str(test_file))})
            print(tuple(s2))
            print()
            """
        )

        def get_printed_value(step_output: str) -> str:
            """Extract the printed value from pdb step output."""
            lines = step_output.strip().split("\n")
            return lines[1].strip()

        # 1. Advance to line 3 (first print of statvfs)
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)

        # 2. Execute line 3: print statvfs -> statvfs_before
        d.sendline("n")
        step_out = d.assert_line_number(4)
        statvfs_before = get_printed_value(step_out)

        # 3. Execute line 4 (write data), line 5 (re-statvfs), line 6 (print)
        d.sendline("n")
        d.assert_line_number(5)
        d.sendline("n")
        d.assert_line_number(6)
        d.sendline("n")
        step_out = d.assert_line_number(7)
        statvfs_after = get_printed_value(step_out)

        # 4. Step back to line 3 and replay to verify determinism
        d.sendline("back")
        d.assert_line_number(6)
        d.sendline("back")
        d.assert_line_number(5)
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("back")
        d.assert_line_number(3)

        # Re-execute line 3
        d.sendline("n")
        step_out = d.assert_line_number(4)
        statvfs_before_replay = get_printed_value(step_out)
        assert statvfs_before == statvfs_before_replay, (
            f"statvfs before mismatch: {statvfs_before} vs {statvfs_before_replay}"
        )

        # Re-execute lines 4-6
        d.sendline("n")
        d.assert_line_number(5)
        d.sendline("n")
        d.assert_line_number(6)
        d.sendline("n")
        step_out = d.assert_line_number(7)
        statvfs_after_replay = get_printed_value(step_out)
        assert statvfs_after == statvfs_after_replay, (
            f"statvfs after mismatch: {statvfs_after} vs {statvfs_after_replay}"
        )

        d.quit()


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

        # 4. Verify statvfs follows the symlink (same filesystem => same result)
        assert statvfs_symlink == statvfs_target, (
            f"os.statvfs should return the same result for symlink and target.\n"
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


def test_getcwd():
    """Test that os.chdir updates the cwd but stepping back restores
    state effectively for getcwd."""
    with tempfile.TemporaryDirectory() as tmpdir:
        d = launch_dejaview(
            f"""
            import os                # Line 1
            print(os.getcwd())       # Line 2
            os.chdir({repr(tmpdir)}) # Line 3
            print(os.getcwd())       # Line 4
            print()                  # Line 5
            """
        )

        def get_printed_cwd(step_output: str) -> str:
            """
            Extract the printed cwd from pdb step output.
            """
            lines = step_output.strip().split("\n")
            return lines[1].strip()

        # 1. Advance to line 2
        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)

        # 2. Execute Line 2: print(os.getcwd()) -> original_cwd
        d.sendline("n")
        step_out = d.assert_line_number(3)
        original_cwd = get_printed_cwd(step_out)

        # 3. Execute Line 3: os.chdir()
        d.sendline("n")
        d.assert_line_number(4)

        # 4. Execute Line 4: print(os.getcwd()) -> tmp_cwd
        d.sendline("n")
        step_out = d.assert_line_number(5)
        tmp_cwd = get_printed_cwd(step_out)

        # 5. Verify directory changed
        assert original_cwd != tmp_cwd, (
            f"CWD should have changed. Got {original_cwd} twice."
        )

        # 6. Step back to line 2 and re-execute lines 2-4 to verify replay
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("back")
        d.assert_line_number(2)

        # 7. Re-execute line 2: print(os.getcwd()) -> original_cwd_replay
        d.sendline("n")
        step_out = d.assert_line_number(3)
        original_cwd_replay = get_printed_cwd(step_out)
        assert original_cwd == original_cwd_replay, (
            f"Original CWD mismatch: {original_cwd!r} vs {original_cwd_replay!r}"
        )

        # 8. Re-execute line 3: os.chdir()
        d.sendline("n")
        d.assert_line_number(4)

        # 9. Re-execute line 4: print(os.getcwd()) -> tmp_cwd_replay
        d.sendline("n")
        step_out = d.assert_line_number(5)
        tmp_cwd_replay = get_printed_cwd(step_out)
        assert tmp_cwd == tmp_cwd_replay, (
            f"Tmp CWD mismatch: {tmp_cwd!r} vs {tmp_cwd_replay!r}"
        )

        d.quit()


def test_urandom():
    """Test that os.urandom is deterministic."""
    import_stmt = "import os"
    expr = "os.urandom(16).hex()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        expr=expr,
    )
