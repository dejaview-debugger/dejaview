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
    test_file.chmod(0o644)
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


# ==============================================================================
# Iterator-returning functions
# ==============================================================================


def test_scandir_delete_file(tmp_path):
    """Test that os.scandir reflects a deleted file and replays deterministically."""
    (tmp_path / "a.txt").touch()
    (tmp_path / "b.txt").touch()
    (tmp_path / "c.txt").touch()
    tmpdir = str(tmp_path)
    deleted_file = str(tmp_path / "b.txt")

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
    (tmp_path / "a.txt").touch()
    (tmp_path / "b.txt").touch()
    (tmp_path / "c.txt").touch()
    tmpdir = str(tmp_path)
    new_file = str(tmp_path / "new_file.txt")

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
    test_file = tmp_path / "readwrite_test.txt"
    test_file.write_bytes(b"hello")
    fp = str(test_file)

    d = launch_dejaview(
        f"""
        import os
        fd = os.open({fp!r}, os.O_RDWR)
        first = os.read(fd, 5)
        os.lseek(fd, 0, os.SEEK_SET)
        count = os.write(fd, b'hello')
        os.close(fd)
        fd2 = os.open({fp!r}, os.O_RDONLY)
        second = os.read(fd2, 5)
        os.close(fd2)
        result = (first, count, second)
        print(result)
        assert result == (b"hello", 5, b"hello")
        """
    )
    d.run_twice_assert_equal()
    d.quit()
