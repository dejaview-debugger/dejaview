import re
import time

from dejaview.tests.util import launch_dejaview


def test_reverse_step():
    d = launch_dejaview(
        """
        print()         # Line 1
        print(100 + 1)  # Line 2
        print()         # Line 3
        """
    )
    assert "Line 1" in d.expect_prompt()
    d.sendline("n")
    assert "Line 2" in d.expect_prompt()
    d.sendline("n")
    assert "101" in d.expect_prompt()
    d.sendline("back")
    assert "Line 2" in d.expect_prompt()
    d.sendline("back")
    assert "Line 1" in d.expect_prompt()
    d.sendline("n")
    assert "Line 2" in d.expect_prompt()
    d.sendline("n")
    assert "101" in d.expect_prompt()
    d.quit()


def test_extend_head():
    time0 = time.time()

    d = launch_dejaview(
        """
        import time                  # Line 1
        print()                      # Line 2
        print("time:", time.time())  # Line 3
        print("time:", time.time())  # Line 4
        print()                      # Line 5
        """
    )

    def get_time(output: str) -> float:
        match = re.search(r"time: ([0-9]+\.[0-9]+)", output)
        assert match is not None
        return float(match[1])

    # run to line 3
    assert "Line 1" in d.expect_prompt()
    d.sendline("n")
    assert "Line 2" in d.expect_prompt()
    d.sendline("n")
    assert "Line 3" in d.expect_prompt()
    d.sendline("n")
    out = d.expect_prompt()
    assert "Line 4" in out
    time1 = get_time(out)
    assert time1 >= time0

    # rerun line 3
    d.sendline("back")
    assert "Line 3" in d.expect_prompt()
    d.sendline("n")
    out = d.expect_prompt()
    assert "Line 4" in out
    assert time1 == get_time(out)

    # run line 4
    d.sendline("n")
    out = d.expect_prompt()
    assert "Line 5" in out
    time2 = get_time(out)
    assert time2 > time1

    # rerun lines 3 and 4
    d.sendline("back")
    assert "Line 4" in d.expect_prompt()
    d.sendline("back")
    assert "Line 3" in d.expect_prompt()
    d.sendline("n")
    out = d.expect_prompt()
    assert "Line 4" in out
    assert time1 == get_time(out)
    d.sendline("n")
    out = d.expect_prompt()
    assert "Line 5" in out
    assert time2 == get_time(out)

    d.quit()


def test_pid():
    d = launch_dejaview(
        """
        import os                   # Line 1
        pid1 = os.getpid()          # Line 2
        assert pid1 == os.getpid()  # Line 3
        """
    )

    assert "Line 1" in d.expect_prompt()
    d.sendline("n")
    assert "Line 2" in d.expect_prompt()
    d.sendline("n")
    assert "Line 3" in d.expect_prompt()
    d.sendline("back")
    assert "Line 2" in d.expect_prompt()
    d.sendline("c")
    assert "AssertionError" not in d.expect_prompt()
    d.quit()


def test_reverse_continue():
    d = launch_dejaview(
        """
        print()              # Line 1
        print("hit")         # Line 2
        print()              # Line 3
        print("after hit")   # Line 4
        print("end")         # Line 5
        """
    )

    assert "Line 1" in d.expect_prompt()

    d.sendline("b 4")
    out = d.expect_prompt()
    assert "Breakpoint" in out

    d.sendline("c")
    out = d.expect_prompt()
    assert "Line 4" in out

    d.sendline("b 2")
    out = d.expect_prompt()
    assert "Breakpoint" in out

    d.sendline("rc")
    out = d.expect_prompt()
    assert "Line 2" in out

    d.sendline("c")
    out = d.expect_prompt()
    assert "Line 4" in out

    d.sendline("n")
    out = d.expect_prompt()
    assert "Line 5" in out

    d.sendline("rc")
    out = d.expect_prompt()
    assert "Line 4" in out

    d.quit()


def test_rc_function():
    d = launch_dejaview(
        """
        def foo(x):  # Line 1
            print(4)  # Line 2

        print(2)  # Line 4
        foo(3)
        print(3)  # Line 6
        """
    )

    assert "Line 1" in d.expect_prompt()
    d.sendline("b 4")
    d.expect_prompt()
    d.sendline("b 6")
    d.expect_prompt()
    d.sendline("c")
    assert "Line 4" in d.expect_prompt()
    d.sendline("c")
    assert "Line 6" in d.expect_prompt()
    d.sendline("rc")
    assert "Line 4" in d.expect_prompt()
    d.sendline("c")
    assert "Line 6" in d.expect_prompt()
    d.quit()


def test_extend_head_step_over():
    d = launch_dejaview(
        """
        a = 1234      # Line 1
        def foo():    # Line 2
            print(a)  # Line 3
            print(a)  # Line 4
        print(a)      # Line 5
        foo()         # Line 6
        print(a)      # Line 7
        """
    )
    assert "Line 1" in d.expect_prompt()
    d.sendline("b 4")
    d.expect_prompt()
    d.sendline("c")
    assert "Line 4" in d.expect_prompt()
    d.sendline("b 6")
    d.expect_prompt()
    d.sendline("rc")
    assert "Line 6" in d.expect_prompt()
    d.sendline("clear 1 2")
    d.expect_prompt()
    d.sendline("n")  # step over
    out = d.expect_prompt()
    assert "Line 7" in out
    assert out.count("1234") == 2


def test_extend_head_step_out():
    d = launch_dejaview(
        """
        a = 1234      # Line 1
        def foo():    # Line 2
            print(a)  # Line 3
            print(a)  # Line 4
        print(a)      # Line 5
        foo()         # Line 6
        print(a)      # Line 7
        """
    )
    assert "Line 1" in d.expect_prompt()
    d.sendline("b 4")
    d.expect_prompt()
    d.sendline("c")
    assert "Line 4" in d.expect_prompt()
    d.sendline("back")
    assert "Line 3" in d.expect_prompt()
    d.sendline("clear 1")
    d.expect_prompt()
    d.sendline("return")  # step out
    out = d.expect_prompt()
    assert "--Return--" in out
    assert out.count("1234") == 2


def test_extend_head_until():
    d = launch_dejaview(
        """
        a = 1234      # Line 1
        print(a)      # Line 2
        print(a)      # Line 3
        print(a)      # Line 4
        print(a)      # Line 5
        """
    )
    assert "Line 1" in d.expect_prompt()
    d.sendline("b 3")
    d.expect_prompt()
    d.sendline("c")
    assert "Line 3" in d.expect_prompt()
    d.sendline("back")
    assert "Line 2" in d.expect_prompt()
    d.sendline("clear 1")
    d.expect_prompt()
    d.sendline("until 5")  # step until
    out = d.expect_prompt()
    assert "Line 5" in out
    assert out.count("1234") == 3


def test_finish():
    d = launch_dejaview(
        """
        print()         # Line 1
        print()         # Line 2
        """
    )
    assert "Line 1" in d.expect_prompt()
    d.sendline("c")
    output = d.expect_prompt()
    assert "The program finished and will be restarted" in output
    assert "Line 1" in output
    d.sendline("n")
    assert "Line 2" in d.expect_prompt()
    d.sendline("back")
    assert "Line 1" in d.expect_prompt()
    d.quit()


def test_restart():
    d = launch_dejaview(
        """
        print()         # Line 1
        print()         # Line 2
        """
    )
    assert "Line 1" in d.expect_prompt()
    d.sendline("n")
    assert "Line 2" in d.expect_prompt()
    d.sendline("restart")
    assert "Line 1" in d.expect_prompt()
    d.sendline("n")
    assert "Line 2" in d.expect_prompt()
    d.sendline("back")
    assert "Line 1" in d.expect_prompt()
    d.quit()
