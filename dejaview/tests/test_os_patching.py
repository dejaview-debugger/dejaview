import os as _real_os
from pathlib import Path

from dejaview.tests.util import launch_dejaview

# ==============================================================================
# Process / user identity
# ==============================================================================


def test_getpid():
    """Test that os.getpid is deterministic."""
    d = launch_dejaview(
        """
        import os
        v1 = os.getpid()
        v2 = os.getpid()
        assert v1 == v2
        print(v1, v2)
        """
    )
    d.run_twice_assert_equal()
    d.quit()


# ==============================================================================
# Filesystem queries
# ==============================================================================


def test_listdir(tmp_path):
    """Test that os.listdir is deterministic."""
    tmpdir = str(tmp_path)
    Path(tmpdir, "a.txt").touch()
    Path(tmpdir, "b.txt").touch()

    d = launch_dejaview(
        f"""
        import os
        print(sorted(os.listdir({tmpdir!r})))
        open(os.path.join({tmpdir!r}, "new_file.txt"), "w").close()
        after = sorted(os.listdir({tmpdir!r}))
        print(after)
        assert "new_file.txt" in after
        """
    )
    d.run_twice_assert_equal()
    d.quit()


def test_stat(tmp_path):
    """Test that os.stat is deterministic across a file mutation."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello")
    _real_os.chmod(str(test_file), 0o644)
    fp = str(test_file)

    d = launch_dejaview(
        f"""
        import os
        s = os.stat({fp!r})
        before = (s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink)
        print(before)
        os.chmod({fp!r}, 0o755)
        with open({fp!r}, 'a') as f:
            f.write(' world')
        s = os.stat({fp!r})
        after = (s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink)
        print(after)
        assert before != after
        """
    )
    d.run_twice_assert_equal()
    d.quit()


def test_stat_symlink(tmp_path):
    """Test that patched os.stat follows symlinks and is deterministic."""
    target_file = tmp_path / "target.txt"
    target_file.write_text("hello")
    _real_os.chmod(str(target_file), 0o644)

    symlink_path = tmp_path / "link.txt"
    symlink_path.symlink_to(target_file)
    sp = str(symlink_path)
    tp = str(target_file)

    d = launch_dejaview(
        f"""
        import os
        st = os.stat({sp!r})
        before = (st.st_mode, st.st_size, st.st_uid, st.st_gid, st.st_nlink)
        print(before)
        os.chmod({tp!r}, 0o755)
        with open({tp!r}, 'a') as f:
            f.write(' world')
        st = os.stat({sp!r})
        after = (st.st_mode, st.st_size, st.st_uid, st.st_gid, st.st_nlink)
        print(after)
        assert before != after, "Expected symlink stat to reflect target changes"
        """
    )
    d.run_twice_assert_equal()
    d.quit()


def test_lstat(tmp_path):
    """Test that os.lstat is deterministic across a file mutation."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello")
    _real_os.chmod(str(test_file), 0o644)
    fp = str(test_file)

    d = launch_dejaview(
        f"""
        import os
        s = os.lstat({fp!r})
        before = (s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink)
        print(before)
        os.chmod({fp!r}, 0o755)
        with open({fp!r}, 'a') as f:
            f.write(' world')
        s = os.lstat({fp!r})
        after = (s.st_mode, s.st_size, s.st_uid, s.st_gid, s.st_nlink)
        print(after)
        assert before != after
        """
    )
    d.run_twice_assert_equal()
    d.quit()


def test_lstat_symlink(tmp_path):
    """Test that patched os.lstat does not follow symlinks and is deterministic."""
    target_file = tmp_path / "target.txt"
    target_file.write_text("hello")
    _real_os.chmod(str(target_file), 0o644)

    symlink_path = tmp_path / "link.txt"
    symlink_path.symlink_to(target_file)
    sp = str(symlink_path)
    tp = str(target_file)

    d = launch_dejaview(
        f"""
        import os
        sl = os.lstat({sp!r})
        before = (sl.st_mode, sl.st_size, sl.st_uid, sl.st_gid, sl.st_nlink)
        print(before)
        os.chmod({tp!r}, 0o755)
        with open({tp!r}, 'a') as f:
            f.write(' world')
        sl = os.lstat({sp!r})
        after = (sl.st_mode, sl.st_size, sl.st_uid, sl.st_gid, sl.st_nlink)
        print(after)
        assert before == after, "lstat on symlink should not change"
        """
    )
    d.run_twice_assert_equal()
    d.quit()


def test_statvfs(tmp_path):
    """Test that os.statvfs is deterministic."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello")
    fp = str(test_file)

    d = launch_dejaview(
        f"""
        import os
        s = os.statvfs({fp!r})
        print(tuple(s))
        with open({fp!r}, 'a') as f:
            f.write('x' * 10000)
        s = os.statvfs({fp!r})
        print(tuple(s))
        """
    )
    d.run_twice_assert_equal()
    d.quit()


def test_statvfs_symlink(tmp_path):
    """Test that os.statvfs on a symlink is deterministic and follows the symlink."""
    target_file = tmp_path / "target.txt"
    target_file.write_text("hello")

    symlink_path = tmp_path / "link.txt"
    symlink_path.symlink_to(target_file)
    sp = str(symlink_path)

    d = launch_dejaview(
        f"""
        import os
        sl = os.statvfs({sp!r})
        print(tuple(sl))
        """
    )
    d.run_twice_assert_equal()
    d.quit()

    # Verify that statvfs follows the symlink (same filesystem as target)
    def stable_fields(t):
        # Indices 3,4,6,7 (free block/inode counts) are volatile on busy systems
        return tuple(t[i] for i in (0, 1, 2, 5, 8, 9))

    sl = _real_os.statvfs(str(symlink_path))
    tl = _real_os.statvfs(str(target_file))
    assert stable_fields(tuple(sl)) == stable_fields(tuple(tl)), (
        "os.statvfs should return the same filesystem for symlink and target"
    )


def test_urandom():
    """Test that os.urandom is deterministic."""
    d = launch_dejaview(
        """
        import os
        v1 = os.urandom(16).hex()
        v2 = os.urandom(16).hex()
        assert v1 != v2, "Successive urandom calls should differ"
        print(v1, v2)
        """
    )
    d.run_twice_assert_equal()
    d.quit()


# ==============================================================================
# Side-effect functions
# ==============================================================================


def test_mkdir_replay(tmp_path):
    """Test that os.mkdir is deterministic on replay."""
    non_existent = str(tmp_path / "this_does_not_exist")

    d = launch_dejaview(
        f"""
        import os
        before = os.path.isdir({non_existent!r})
        print(before)
        assert not before
        os.mkdir({non_existent!r})
        after = os.path.isdir({non_existent!r})
        print(after)
        assert after
        """
    )
    d.run_twice_assert_equal()
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
            key = {key!r}
            value = {value!r}
            libc = ctypes.CDLL(None)
            libc.getenv.argtypes = [ctypes.c_char_p]
            libc.getenv.restype = ctypes.c_char_p
            before = libc.getenv(key.encode()) is not None
            print(before)
            assert not before
            os.putenv(key, value)
            after = libc.getenv(key.encode()) is not None
            print(after)
            assert after
            """
        )
        d.run_twice_assert_equal()
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
            key = {key!r}
            seed = {seed!r}
            libc = ctypes.CDLL(None)
            libc.getenv.argtypes = [ctypes.c_char_p]
            libc.getenv.restype = ctypes.c_char_p
            os.putenv(key, seed)
            before = libc.getenv(key.encode()) is not None
            print(before)
            assert before
            os.unsetenv(key)
            after = libc.getenv(key.encode()) is not None
            print(after)
            assert not after
            """
        )
        d.run_twice_assert_equal()
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


def test_scandir_delete_file(tmp_path):
    """Test that os.scandir reflects a deleted file and replays deterministically."""
    tmpdir = str(tmp_path)
    Path(tmpdir, "a.txt").touch()
    Path(tmpdir, "b.txt").touch()
    Path(tmpdir, "c.txt").touch()
    deleted_file = str(Path(tmpdir, "b.txt"))

    d = launch_dejaview(
        f"""
        import os
        before = sorted(e.name for e in os.scandir({tmpdir!r}))
        print(before)
        assert before == ["a.txt", "b.txt", "c.txt"]
        os.remove({deleted_file!r})
        after = sorted(e.name for e in os.scandir({tmpdir!r}))
        print(after)
        assert after == ["a.txt", "c.txt"]
        """
    )
    d.run_twice_assert_equal()
    d.quit()


def test_scandir_add_file(tmp_path):
    """Test that os.scandir reflects an added file and replays deterministically."""
    tmpdir = str(tmp_path)
    Path(tmpdir, "a.txt").touch()
    Path(tmpdir, "b.txt").touch()
    Path(tmpdir, "c.txt").touch()
    new_file = str(Path(tmpdir, "new_file.txt"))

    d = launch_dejaview(
        f"""
        import os
        before = sorted(e.name for e in os.scandir({tmpdir!r}))
        print(before)
        assert before == ["a.txt", "b.txt", "c.txt"]
        os.open({new_file!r}, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        after = sorted(e.name for e in os.scandir({tmpdir!r}))
        print(after)
        assert after == ["a.txt", "b.txt", "c.txt", "new_file.txt"]
        """
    )
    d.run_twice_assert_equal()
    d.quit()


# ==============================================================================
# Low-level I/O
# ==============================================================================


def test_os_open_read(tmp_path):
    """Test that os.open and os.read are deterministic on replay."""
    test_file = tmp_path / "io_test.txt"
    test_file.write_text("hello world")
    fp = str(test_file)

    d = launch_dejaview(
        f"""
        import os
        data = os.read(os.open({fp!r}, os.O_RDONLY), 100)
        print(data)
        assert data == b"hello world"
        """
    )
    d.run_twice_assert_equal()
    d.quit()


def test_os_write(tmp_path):
    """Test that os.write is deterministic on replay."""
    test_file = tmp_path / "write_test.txt"
    fp = str(test_file)

    d = launch_dejaview(
        f"""
        import os
        n = os.write(os.open({fp!r}, os.O_WRONLY | os.O_CREAT), b'hello')
        print(n)
        assert n == 5
        """
    )
    d.run_twice_assert_equal()
    d.quit()


def test_os_read_write(tmp_path):
    """Test that os.read and os.write are deterministic on replay."""
    temp_path = str(tmp_path / "readwrite_test.txt")
    with open(temp_path, "wb") as f:
        f.write(b"hello")

    d = launch_dejaview(
        f"""
        import os
        fd = os.open({temp_path!r}, os.O_RDWR)
        first = os.read(fd, 5)
        os.lseek(fd, 0, os.SEEK_SET)
        count = os.write(fd, b'hello')
        os.close(fd)
        fd2 = os.open({temp_path!r}, os.O_RDONLY)
        second = os.read(fd2, 5)
        os.close(fd2)
        result = (first, count, second)
        print(result)
        assert result == (b"hello", 5, b"hello")
        """
    )
    d.run_twice_assert_equal()
    d.quit()
