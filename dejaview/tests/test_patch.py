import re

import pytest

from dejaview.tests.util import launch_dejaview


def extract_out(stdout: str) -> str:
    """
    extract information from output of the form "out: <...>"
    """
    m = re.search(r"out: (.*)", stdout)
    assert m is not None
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
