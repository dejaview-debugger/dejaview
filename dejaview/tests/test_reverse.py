import re
import time

from dejaview.tests.util import (
    DebugCommand,
    PropertyTester,
    SourceFile,
    launch_dejaview,
)


def test_reverse_step():
    d = launch_dejaview(
        """
        print()         # Line 1
        print(100 + 1)  # Line 2
        print()         # Line 3
        """
    )
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    assert "101" in d.expect_prompt()
    d.sendline("back")
    d.assert_line_number(2)
    d.sendline("back")
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
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
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    d.assert_line_number(3)
    d.sendline("n")
    out = d.assert_line_number(4)
    time1 = get_time(out)
    assert time1 >= time0

    # rerun line 3
    d.sendline("back")
    d.assert_line_number(3)
    d.sendline("n")
    out = d.assert_line_number(4)
    assert time1 == get_time(out)

    # run line 4
    d.sendline("n")
    out = d.assert_line_number(5)
    time2 = get_time(out)
    assert time2 > time1

    # rerun lines 3 and 4
    d.sendline("back")
    d.assert_line_number(4)
    d.sendline("back")
    d.assert_line_number(3)
    d.sendline("n")
    out = d.assert_line_number(4)
    assert time1 == get_time(out)
    d.sendline("n")
    out = d.assert_line_number(5)
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

    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    d.assert_line_number(3)
    d.sendline("back")
    d.assert_line_number(2)
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

    d.assert_line_number(1)

    d.sendline("b 4")
    out = d.expect_prompt()
    assert "Breakpoint" in out

    d.sendline("c")
    d.assert_line_number(4)

    d.sendline("b 2")
    out = d.expect_prompt()
    assert "Breakpoint" in out

    d.sendline("rc")
    d.assert_line_number(2)

    d.sendline("c")
    d.assert_line_number(4)

    d.sendline("n")
    d.assert_line_number(5)

    d.sendline("rc")
    d.assert_line_number(4)

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

    d.assert_line_number(1)
    d.sendline("b 4")
    d.expect_prompt()
    d.sendline("b 6")
    d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(4)
    d.sendline("c")
    d.assert_line_number(6)
    d.sendline("rc")
    d.assert_line_number(4)
    d.sendline("c")
    d.assert_line_number(6)
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
    d.assert_line_number(1)
    d.sendline("b 4")
    d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(4)
    d.sendline("b 6")
    d.expect_prompt()
    d.sendline("rc")
    d.assert_line_number(6)
    d.sendline("clear 1 2")
    d.expect_prompt()
    d.sendline("n")  # step over
    out = d.assert_line_number(7)
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
    d.assert_line_number(1)
    d.sendline("b 4")
    d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(4)
    d.sendline("back")
    d.assert_line_number(3)
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
    d.assert_line_number(1)
    d.sendline("b 3")
    d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(3)
    d.sendline("back")
    d.assert_line_number(2)
    d.sendline("clear 1")
    d.expect_prompt()
    d.sendline("until 5")  # step until
    out = d.assert_line_number(5)
    assert out.count("1234") == 3


def test_finish():
    d = launch_dejaview(
        """
        print("out:", 1)  # Line 1
        print("out:", 2)  # Line 2
        print("out:", 3)  # Line 3
        """
    )
    d.assert_line_number(1)
    d.sendline("c")
    output = d.assert_line_number(1)
    assert "out: 1" in output
    assert "out: 2" in output
    assert "out: 3" in output
    assert "The program finished and will be restarted" in output
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    d.assert_line_number(3)
    d.sendline("back")
    d.assert_line_number(2)
    d.quit()


def test_restart():
    d = launch_dejaview(
        """
        print()         # Line 1
        print()         # Line 2
        """
    )
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("restart")
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("back")
    d.assert_line_number(1)
    d.quit()


def test_random_determinism_control_flow():
    program = """
        import random
        random.seed(0)
        result = []
        for _ in range(5):
            value = random.random()
            # Modify control flow to test state
            if value > 0.5:
                result.append(0)
            else:
                result.append(1)
            print(value)
        """
    sequence = [DebugCommand.STEP for _ in range(23)]
    reverse_seq = [DebugCommand.BACK for _ in range(23)]
    commands = sequence + reverse_seq

    PropertyTester.test_determinism_property(program, commands)


def test_random_idempotence():
    # Similar test to test_random_determinism_control_flow,
    # which verifies random is idempotent when replaying.
    d = launch_dejaview(
        """
        import random
        import time
        random.seed()
        results = []
        for i in range(5):
            value = random.random()
            timestamp = time.time()
            print(timestamp)
            print(value)
        total = len(results)
        """
    )

    # Start at line 1
    d.assert_line_number(1)

    # Step through initialization
    d.sendline("n")  # line 2
    d.expect_prompt()
    d.sendline("n")  # line 3
    d.expect_prompt()
    d.sendline("n")  # line 4
    d.expect_prompt()

    # Now we're at line 5, about to enter the loop
    # Test idempotence through several iterations of the loop
    PropertyTester.test_idempotence_property(d, forward_steps=8)

    # Move forward into the middle of the loop
    for _ in range(12):
        d.sendline("n")
        d.expect_prompt()

    # Test idempotence again from a different point
    # This should replay the same random values and timestamps
    PropertyTester.test_idempotence_property(d, forward_steps=5)

    d.quit()


def test_rc_2_files():
    d = launch_dejaview(
        SourceFile(
            "a.py",
            """
            import b     # Line a1
            print("a2")  # Line a2
            b.foo()      # Line a3
            print("a4")  # Line a4
            """,
        ),
        SourceFile(
            "b.py",
            """
            def foo():        # Line b1
                print("b2")   # Line b2
                print("b3")   # Line b3
            """,
        ),
    )

    assert "Line a1" in d.expect_prompt()
    d.sendline("b b.py:2")
    d.expect_prompt()
    d.sendline("c")
    assert "Line b2" in d.expect_prompt()
    d.sendline("n")
    assert "Line b3" in d.expect_prompt()
    d.sendline("rc")
    assert "Line b2" in d.expect_prompt()
    d.quit()
