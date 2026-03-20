import re
import time
import traceback
from pathlib import Path

import pytest

from dejaview.patching.patching import (
    Patches,
    PatchingMode,
    capture,
    reset,
    set_patching_mode,
)
from dejaview.patching.state_store import StateStore
from dejaview.patching.util import hide_from_traceback
from dejaview.tests.util import launch_dejaview


def extract_out(stdout: str) -> str:
    """
    extract information from output of the form "out: <...>"
    """
    m = re.search(r"out: (.*)", stdout)
    assert m is not None, stdout
    return m[1]


def extract_out_multiline(stdout: str) -> str:
    """
    extract information from output of the form:
    === BEGIN OUT ===
    ...
    === END OUT ===
    """
    pattern = r"=== BEGIN OUT ===\r?\n(.*?)\r?\n=== END OUT ==="
    m = re.search(pattern, stdout, re.DOTALL)
    assert m is not None, stdout
    return m[1]


@pytest.mark.parametrize("value", ["42", "'hi'", "object()"])
def test_hash_same_object(value: str):
    d = launch_dejaview(
        f"""
        a = {value}                # Line 1
        h1 = hash(a)               # Line 2
        h2 = hash(a)               # Line 3
        assert h1 == h2, (h1, h2)  # Line 4
        """
    )

    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    d.assert_line_number(3)
    d.sendline("back")
    d.assert_line_number(2)
    d.sendline("c")
    result = d.expect_prompt()
    print(result)
    assert "AssertionError" not in result
    d.quit()


@pytest.mark.parametrize("fn", ["id", "hash"])
def test_hash_new_object(fn: str):
    d = launch_dejaview(
        f"""
        print("out:", {fn}(object()))  # Line 1
        print(1)                       # Line 2
        """
    )

    d.assert_line_number(1)
    d.sendline("n")
    out = d.assert_line_number(2)
    x1 = extract_out(out)
    d.sendline("back")
    d.assert_line_number(1)
    d.sendline("c")
    out = d.expect_prompt()
    x2 = extract_out(out)
    assert x1 == x2
    d.quit()


@pytest.mark.parametrize("cls", ["set", "frozenset"])
def test_set_order(cls: str):
    d = launch_dejaview(
        f"""
        class A:
            def __init__(self, x):
                self.x = x
            def __repr__(self):
                return repr(self.x)
        objs = [A(x) for x in range(20)]
        print("hash:", [hash(o) for o in objs])
        print("o.__hash__:", [o.__hash__() for o in objs])
        print("object.__hash__:", [object.__hash__(o) for o in objs])
        print("id:", [id(o) for o in objs])
        order = list({cls}(objs))
        print("out:", order)
        """
    )

    d.expect_prompt()
    d.sendline("c")
    out = d.expect_prompt()
    print(out)
    assert "The program finished and will be restarted" in out
    x1 = extract_out(out)
    d.sendline("c")
    out = d.expect_prompt()
    print(out)
    x2 = extract_out(out)
    assert x1 == x2
    d.quit()


def test_id_patch_disable():
    from dejaview.patching.setup import memory_patch  # noqa: PLC0415

    objs = [object() for _ in range(20)]
    before_ids = [id(o) for o in objs]
    before_hashes = [hash(o) for o in objs]
    before_order = list(set(objs))

    with memory_patch():
        ids = [id(o) for o in objs]
        hashes = [hash(o) for o in objs]
        order = list(set(objs))
        assert ids != before_ids
        assert ids == hashes
        assert order != before_order

    after_ids = [id(o) for o in objs]
    after_hashes = [hash(o) for o in objs]
    after_order = list(set(objs))
    assert after_ids == before_ids
    assert after_hashes == before_hashes
    assert after_order == before_order


def test_hide_from_traceback():
    def f1():
        raise ValueError("error in f1")

    @hide_from_traceback
    def f2():
        f1()

    def f3():
        f2()

    try:
        f3()
    except ValueError:
        tb = traceback.format_exc()
        print(tb)
        assert "in f1" in tb
        assert "in f2" not in tb
        assert "in hide_from_traceback" not in tb
        assert "in f3" in tb


def test_exception_traceback():
    d = launch_dejaview(
        """
        import socket
        import traceback
        try:
            socket.socket(family=9999)  # invalid family
        except OSError:
            print("=== BEGIN OUT ===")
            print(traceback.format_exc())
            print("=== END OUT ===")
        print()
        """
    )

    d.assert_line_number(1)
    d.sendline("c")
    out1 = extract_out_multiline(d.expect_prompt()).strip()
    # The patching code is hidden from the traceback
    assert "dejaview/patching" not in out1
    # Frames inside the patched function show up
    assert "socket.py" in out1
    # The caller frame also shows up
    assert "socket.socket(family=9999)" in out1

    d.sendline("c")
    out2 = extract_out_multiline(d.expect_prompt()).strip()
    assert out1 == out2
    d.quit()


def test_should_patch():
    num = 0

    class Foo:
        def add(self, x):
            nonlocal num
            num += x
            return num

    with Patches() as p, set_patching_mode(PatchingMode.NORMAL):
        p.patch(Foo, "add", should_patch=lambda self, x: x == 2)
        store = StateStore.get(Foo.add).store
        assert len(store) == 0

        foo = Foo()
        assert foo.add(1) == 1
        assert len(store) == 0  # not patched
        assert foo.add(2) == 3
        assert len(store) == 1  # patched
        assert foo.add(3) == 6
        assert len(store) == 1  # not patched


def test_recursive_patch():
    class Foo:
        @staticmethod
        def f1(x):
            return x

        @staticmethod
        def f2():
            return Foo.f1(123)

    with Patches() as p, set_patching_mode(PatchingMode.NORMAL):
        p.patch(Foo, "f1")
        p.patch(Foo, "f2")

        # play
        state = capture()
        assert Foo.f1(1) == 1
        assert Foo.f2() == 123
        assert Foo.f1(2) == 2

        # replay should produce the same sequence
        reset(state)
        assert Foo.f1(1) == 1
        assert Foo.f2() == 123
        assert Foo.f1(2) == 2


def test_localtime():
    d = launch_dejaview(
        """
        import time
        print("out:", time.localtime())
        print("out:", time.localtime(1234))
        """
    )

    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    x1 = extract_out(d.assert_line_number(3))
    d.sendline("back")
    d.assert_line_number(2)
    d.sendline("n")
    x2 = extract_out(d.assert_line_number(3))
    assert x1 == x2
    d.sendline("c")
    out = extract_out(d.expect_prompt()).strip()
    assert out == str(time.localtime(1234))
    d.quit()


def test_sys_stdout_write():
    d = launch_dejaview(
        r"""
        import sys
        sys.stdout.write("12" + "34\n")
        3
        4
        """
    )

    d.expect_prompt()
    out1 = d.send_command("until 4")
    assert "1234" in out1
    out2 = d.send_command("rs")
    assert "1234" not in out2  # properly muted on replay
    d.quit()


def test_sys_stdin_readline():
    d = launch_dejaview(
        """
        import sys
        line = sys.stdin.readline()
        print("out:", line)
        """
    )

    d.expect_prompt()
    d.sendline("c")
    d.sendline("hello_stdin")
    out = d.expect_prompt()
    x1 = extract_out(out)
    assert "hello_stdin" in x1

    out = d.send_command("c")
    x2 = extract_out(out)
    assert x1 == x2
    d.quit()


def test_io_patch_isinstance():
    """Objects created under io_patch should still pass isinstance(obj, io.XBase)."""
    d = launch_dejaview(
        """
        import io
        f = io.BytesIO(b"hello")
        tw = io.TextIOWrapper(f)
        checks = [
            isinstance(f, io.BufferedIOBase),
            isinstance(f, io.IOBase),
            isinstance(tw, io.TextIOBase),
            isinstance(tw, io.IOBase),
        ]
        print("out:", all(checks))
        """
    )

    d.expect_prompt()
    out = d.send_command("c")
    x = extract_out(out).strip()
    assert x == "True"
    d.quit()


@pytest.mark.xfail(reason="os module not yet patched", strict=True)
def test_file_read(tmp_path: Path):
    path = tmp_path / "test_file"
    path.write_text("hello")
    d = launch_dejaview(
        f"""
        with open("{path}", "r") as f:
            data = f.read()
        print("out:", data)
        """
    )

    d.expect_prompt()
    out = d.send_command("c")
    x1 = extract_out(out)
    assert "hello" in x1

    path.write_text("world")  # modify file after first read
    out = d.send_command("c")
    x2 = extract_out(out)
    assert x1 == x2
    d.quit()


@pytest.mark.xfail(reason="os module not yet patched", strict=True)
def test_file_write(tmp_path: Path):
    path = tmp_path / "test_file"

    d = launch_dejaview(
        f"""
        with open("{path}", "w") as f:
            f.write("hello")
        """
    )

    d.expect_prompt()
    d.send_command("c")
    assert path.exists()
    path.unlink()

    d.send_command("c")
    assert not path.exists(), "file was written again on replay"
    d.quit()
