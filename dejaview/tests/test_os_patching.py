import ast
import operator
import os as _real_os
from pathlib import Path

from dejaview.tests.util import (
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
        read_stmts="print(os.getpid())",
        compare=operator.eq,
    )


# ==============================================================================
# Filesystem queries
# ==============================================================================


def test_listdir(tmp_path):
    """Test that os.listdir is deterministic."""
    tmpdir = str(tmp_path)
    Path(tmpdir, "a.txt").touch()
    Path(tmpdir, "b.txt").touch()

    before, after = verify_deterministic_mutated_value_util(
        imports="import os",
        read_stmts=f"print(sorted(os.listdir({repr(tmpdir)})))",
        mutate_stmts=(
            f"open(os.path.join({repr(tmpdir)}, 'new_file.txt'), 'w').close()"
        ),
        parse_value=lambda out: ast.literal_eval(out.strip()),
    )
    assert set(before).issubset(set(after)), (
        f"Expected {before} to be a subset of {after}"
    )
    assert "new_file.txt" in after, f"Expected 'new_file.txt' in {after}"


def test_stat(tmp_path):
    """Test that os.stat is deterministic.

    Changes the file's permissions and size, then verifies that stepping
    back and replaying os.stat produces the same metadata both times.
    """
    test_file = tmp_path / "test.txt"
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


def test_stat_symlink(tmp_path):
    """Test that patched os.stat follows symlinks and is deterministic.

    Creates a real file and a symlink to it, then verifies that os.stat on
    the symlink returns the target file's metadata (follows the link),
    and that the result is deterministic on replay.
    """
    target_file = tmp_path / "target.txt"
    target_file.write_text("hello")
    _real_os.chmod(str(target_file), 0o644)

    symlink_path = tmp_path / "link.txt"
    symlink_path.symlink_to(target_file)

    before, after = verify_deterministic_mutated_value_util(
        imports="import os",
        read_stmts=[
            f"st = os.stat({repr(str(symlink_path))})",
            "print((st.st_mode, st.st_size, st.st_uid, st.st_gid, st.st_nlink))",
        ],
        mutate_stmts=[
            f"os.chmod({repr(str(target_file))}, 0o755)",
            f"with open({repr(str(target_file))}, 'a') as f: f.write(' world')",
        ],
        parse_value=lambda out: ast.literal_eval(out.strip()),
    )

    # Verify that stat values changed after mutation (target was modified)
    assert before != after, (
        f"Expected symlink stat to reflect target changes, "
        f"but before={before} and after={after}"
    )


def test_lstat(tmp_path):
    """Test that os.lstat is deterministic.

    Changes permissions and size, then verifies that stepping back and
    replaying os.lstat produces the same metadata both times.
    """
    test_file = tmp_path / "test.txt"
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


def test_lstat_symlink(tmp_path):
    """Test that patched os.lstat does not follow symlinks and is deterministic.

    Creates a real file and a symlink, then verifies that os.lstat on the
    symlink returns metadata about the symlink itself (not the target),
    and that this is deterministic on replay.
    """
    target_file = tmp_path / "target.txt"
    target_file.write_text("hello")
    _real_os.chmod(str(target_file), 0o644)

    symlink_path = tmp_path / "link.txt"
    symlink_path.symlink_to(target_file)

    before, after = verify_deterministic_mutated_value_util(
        imports="import os",
        read_stmts=[
            f"sl = os.lstat({repr(str(symlink_path))})",
            "print((sl.st_mode, sl.st_size, sl.st_uid, sl.st_gid, sl.st_nlink))",
        ],
        mutate_stmts=[
            f"os.chmod({repr(str(target_file))}, 0o755)",
            f"with open({repr(str(target_file))}, 'a') as f: f.write(' world')",
        ],
        parse_value=lambda out: ast.literal_eval(out.strip()),
        assert_changed=False,  # lstat on symlink doesn't change when target changes
    )

    # Verify os.lstat does NOT follow the symlink
    # (symlink stats should be the same before and after target mutation)
    assert before == after, (
        f"Symlink lstat should not change when target is modified.\\n"
        f"  before: {before}\\n"
        f"  after:  {after}"
    )


def test_statvfs(tmp_path):
    """Test that os.statvfs is deterministic.

    Writes data to change disk usage, then verifies that stepping back
    and replaying os.statvfs produces the same result both times.
    """
    test_file = tmp_path / "test.txt"
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


def test_statvfs_symlink(tmp_path):
    """Test that os.statvfs on a symlink is deterministic and follows the symlink.

    Creates a target file and a symlink, calls os.statvfs on both, verifies
    they return the same filesystem info, then steps back and replays.
    """
    target_file = tmp_path / "target.txt"
    target_file.write_text("hello")

    symlink_path = tmp_path / "link.txt"
    symlink_path.symlink_to(target_file)

    def stable_statvfs_fields(t: tuple) -> tuple[int, ...]:
        """Extract only the stable structural fields from a statvfs tuple.

        statvfs indices: 0=f_bsize, 1=f_frsize, 2=f_blocks, 3=f_bfree,
        4=f_bavail, 5=f_files, 6=f_ffree, 7=f_favail, 8=f_flag, 9=f_namemax.

        Fields 3,4,6,7 (free block/inode counts) are volatile and can change
        between calls on a busy system, so we only compare the rest.
        """
        return tuple(t[i] for i in (0, 1, 2, 5, 8, 9))

    verify_deterministic_memoized_value_util(
        imports="import os",
        read_stmts=[
            f"sl = os.statvfs({repr(str(symlink_path))})",
            "print(tuple(sl))",
        ],
        parse_value=lambda out: stable_statvfs_fields(ast.literal_eval(out.strip())),
    )

    # Also verify that statvfs follows the symlink (same filesystem as target)
    sl = _real_os.statvfs(str(symlink_path))
    tl = _real_os.statvfs(str(target_file))
    symlink_fields = stable_statvfs_fields(tuple(sl))
    target_fields = stable_statvfs_fields(tuple(tl))
    assert symlink_fields == target_fields, (
        f"os.statvfs should return the same filesystem for symlink and target.\n"
        f"  symlink statvfs: {symlink_fields}\n"
        f"  target statvfs:  {target_fields}"
    )


def test_urandom():
    """Test that os.urandom is deterministic."""
    import_stmt = "import os"
    expr = "os.urandom(16).hex()"

    verify_deterministic_memoized_value_util(
        imports=import_stmt,
        read_stmts=f"print({expr})",
        compare=operator.ne,
    )


# ==============================================================================
# Side-effect functions
# ==============================================================================


def test_mkdir_replay(tmp_path):
    """Test that os.mkdir is deterministic on replay.

    Verifies that mkdir can be recorded and replayed deterministically
    """
    non_existent = str(tmp_path / "this_does_not_exist")

    before, after = verify_deterministic_mutated_value_util(
        imports="import os",
        read_stmts=[
            f"print(os.path.isdir({repr(non_existent)}))",
        ],
        mutate_stmts=[
            f"os.mkdir({repr(non_existent)})",
        ],
        parse_value=lambda out: out.strip() == "True",
        assert_changed=True,
    )

    assert before is False, f"Expected path to not exist, got {before}"
    assert after is True, f"Expected path to exist on replay, got {after}"


# --- Environment mutation ---
def test_putenv_replay():
    key = f"DEJAVIEW_TEST_PUTENV_REPLAY_{_real_os.getpid()}_{id(object())}"
    value = "__dejaview_putenv_value__"

    had_original = key in _real_os.environ
    original_value = _real_os.environ.get(key)

    try:
        _real_os.unsetenv(key)
        _real_os.environ.pop(key, None)

        before, after = verify_deterministic_mutated_value_util(
            imports=f"""
            import os
            import ctypes
            key = {repr(key)}
            value = {repr(value)}
            libc = ctypes.CDLL(None)
            libc.getenv.argtypes = [ctypes.c_char_p]
            libc.getenv.restype = ctypes.c_char_p
            """,
            read_stmts="print(libc.getenv(key.encode()) is not None)",
            mutate_stmts="os.putenv(key, value)",
            parse_value=lambda out: ast.literal_eval(out.strip()),
        )

        assert before is False
        assert after is True
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

        before, after = verify_deterministic_mutated_value_util(
            imports=f"""
            import os
            import ctypes
            key = {repr(key)}
            seed = {repr(seed)}
            libc = ctypes.CDLL(None)
            libc.getenv.argtypes = [ctypes.c_char_p]
            libc.getenv.restype = ctypes.c_char_p
            os.putenv(key, seed)
            """,
            read_stmts="print(libc.getenv(key.encode()) is not None)",
            mutate_stmts="os.unsetenv(key)",
            parse_value=lambda out: ast.literal_eval(out.strip()),
        )

        assert before is True
        assert after is False
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


def test_scandir_delete_file(tmp_path):
    """Test that os.scandir reflects a deleted file and replays deterministically."""
    tmpdir = str(tmp_path)
    Path(tmpdir, "a.txt").touch()
    Path(tmpdir, "b.txt").touch()
    Path(tmpdir, "c.txt").touch()
    deleted_file = Path(tmpdir, "b.txt")

    def parse_printed_names(out: str) -> list[str]:
        return ast.literal_eval(out.strip())

    before, after = verify_deterministic_mutated_value_util(
        imports="import os",
        read_stmts=f"print(sorted(e.name for e in os.scandir({repr(tmpdir)})))",
        mutate_stmts=f"os.remove({repr(str(deleted_file))})",
        parse_value=parse_printed_names,
    )

    assert before == ["a.txt", "b.txt", "c.txt"]
    assert after == ["a.txt", "c.txt"]


def test_scandir_add_file(tmp_path):
    """Test that os.scandir reflects an added file and replays deterministically."""
    tmpdir = str(tmp_path)
    Path(tmpdir, "a.txt").touch()
    Path(tmpdir, "b.txt").touch()
    Path(tmpdir, "c.txt").touch()
    new_file = Path(tmpdir, "new_file.txt")

    def parse_printed_names(out: str) -> list[str]:
        return ast.literal_eval(out.strip())

    before, after = verify_deterministic_mutated_value_util(
        imports="import os",
        read_stmts=f"print(sorted(e.name for e in os.scandir({repr(tmpdir)})))",
        mutate_stmts=f"os.open({repr(str(new_file))}, "
        f"os.O_CREAT | os.O_EXCL | os.O_WRONLY)",
        parse_value=parse_printed_names,
    )

    assert before == ["a.txt", "b.txt", "c.txt"]
    assert after == ["a.txt", "b.txt", "c.txt", "new_file.txt"]


# ==============================================================================
# Low-level I/O
# ==============================================================================


def test_os_open_read(tmp_path):
    """Test that os.open and os.read are deterministic on replay."""
    test_file = tmp_path / "io_test.txt"
    test_file.write_text("hello world")

    def parse_printed_value(out: str) -> bytes:
        return ast.literal_eval(out.strip())

    verify_deterministic_memoized_value_util(
        imports="import os",
        read_stmts=f"print(os.read(os.open({repr(str(test_file))}, os.O_RDONLY), 100))",
        parse_value=parse_printed_value,
        compare=lambda v1, v2: v1 == v2 == b"hello world",
    )


def test_os_write(tmp_path):
    """Test that os.write is deterministic on replay."""
    test_file = tmp_path / "write_test.txt"

    def parse_printed_value(out: str) -> int:
        return ast.literal_eval(out.strip())

    verify_deterministic_memoized_value_util(
        imports="import os",
        read_stmts=f"print(os.write(os.open({repr(str(test_file))}, "
        f"os.O_WRONLY | os.O_CREAT), b'hello'))",
        parse_value=parse_printed_value,
        compare=lambda v1, v2: v1 == v2 == 5,
    )


def test_os_read_write(tmp_path):
    """Test that os.read and os.write are deterministic on replay.

    This test reads, modifies, and re-reads the same file, verifying that
    the sequence of operations produces deterministic results on both
    execution and replay.
    """
    temp_path = str(tmp_path / "readwrite_test.txt")
    with open(temp_path, "wb") as f:
        f.write(b"hello")

    verify_deterministic_memoized_value_util(
        imports="import os",
        read_stmts=[
            f"fd = os.open({repr(temp_path)}, os.O_RDWR)",
            "first = os.read(fd, 5)",
            "os.lseek(fd, 0, os.SEEK_SET)",
            "count = os.write(fd, b'hello')",
            "os.close(fd)",
            f"fd2 = os.open({repr(temp_path)}, os.O_RDONLY)",
            "second = os.read(fd2, 5)",
            "os.close(fd2)",
            "print((first, count, second))",
        ],
        parse_value=lambda out: ast.literal_eval(out.strip()),
        compare=lambda v1, v2: v1 == v2 == (b"hello", 5, b"hello"),
    )
