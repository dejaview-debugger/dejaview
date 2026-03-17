import re
import time

from dejaview.tests.util import (
    DebugCommand,
    PropertyTester,
    SourceFile,
    launch_dejaview,
)


def test_print():
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


def test_rstep():
    d = launch_dejaview(
        """
        1
        2
        def bar():
            raise ValueError("error in bar")
        def foo():
            6
            7
            try:
                bar()
            except ValueError:
                pass
        foo()
        13
        14
        """
    )

    # capture forward step states
    expected: list[str] = []
    while True:
        out = d.expect_prompt()
        expected.append(out.removeprefix("step").strip())
        state = d.capture_state()
        d.sendline("step")
        if state.line_number == 14:
            d.expect_prompt()
            break

    # test that reverse step gives the same sequence
    actual: list[str] = []
    for _ in expected:
        d.sendline("rstep")
        out = d.expect_prompt()
        actual.append(out.removeprefix("rstep").strip())
        state = d.capture_state()
        if state.line_number == 1:
            break

    actual.reverse()  # reverse to match forward order
    assert actual == expected

    d.quit()


def test_rnext():
    d = launch_dejaview(
        """
        1
        2
        def foo():
            4
            5
        foo()
        7
        8
        """
    )

    d.assert_line_number(1)
    d.run_to(7)
    d.sendline("rnext")
    d.assert_line_number(6)

    d.restart()
    d.run_to(5)
    d.sendline("rnext")
    d.assert_line_number(4)

    d.restart()
    d.run_to(4)
    d.sendline("rnext")
    assert "--Call--" in d.expect_prompt()

    d.quit()


def test_rreturn():
    d = launch_dejaview(
        """
        1
        2
        def foo():
            4
            5
        foo()
        7
        8
        """
    )

    d.assert_line_number(1)
    d.run_to(5)
    d.sendline("rreturn")
    d.assert_line_number(6)

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


def test_setvar():
    d = launch_dejaview(
        """
        a = "original_value"  # Line 1
        b = 10
        c = [1, 2, 3]         # Line 3
        print()
        """
    )
    d.assert_line_number(1)
    d.sendline("b 3")
    d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(3)
    d.sendline("setvar a 'modified_value'")
    d.assert_line_number(3)
    d.sendline("print(a)")
    assert "modified_value" in d.expect_prompt()
    d.sendline("back")
    d.assert_line_number(2)
    d.sendline("setvar a 'another_value'")
    assert "Cannot modify variables while viewing a historical" in d.expect_prompt()
    d.sendline("print(a)")
    assert "original_value" in d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(3)
    d.sendline("print(a)")
    assert "modified_value" in d.expect_prompt()
    d.sendline("n")
    d.assert_line_number(4)
    d.quit()


def test_setvar_persists_through_replay():
    """setvar mutations should be re-applied when replay reaches the same position."""
    d = launch_dejaview(
        """
        a = "original_value"  # Line 1
        b = 10                # Line 2
        c = [1, 2, 3]         # Line 3
        print()               # Line 4
        """
    )
    d.assert_line_number(1)
    d.sendline("b 3")
    d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(3)
    # Modify the variable at line 3
    d.sendline("setvar a 'modified_value'")
    d.assert_line_number(3)
    d.sendline("print(a)")
    assert "modified_value" in d.expect_prompt()
    # Step back past the setvar position
    d.sendline("back")
    d.assert_line_number(2)
    d.sendline("back")
    d.assert_line_number(1)
    # Step forward again to line 3 — the setvar should be re-applied by the handler
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    d.assert_line_number(3)
    d.sendline("print(a)")
    assert "modified_value" in d.expect_prompt()
    d.quit()


def test_setvar_advanced():
    d = launch_dejaview(
        """
        a = "original_value"  # Line 1
        b = 10
        c = [1, 2, 3]         # Line 3
        print()
        print()
        """
    )
    d.assert_line_number(1)
    d.sendline("b 3")
    d.expect_prompt()
    d.sendline("b 5")
    d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(3)
    d.sendline("setvar a 'modified_value'")
    d.expect_prompt()
    d.sendline("back")
    d.assert_line_number(2)
    d.sendline("c")
    d.assert_line_number(3)
    d.sendline("print(a)")
    assert "modified_value" in d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(5)
    d.sendline("back")
    d.assert_line_number(4)
    d.sendline("print(a)")
    assert "modified_value" in d.expect_prompt()
    d.quit()


def test_setvar_back_twice():
    d = launch_dejaview(
        """
        a = 1234  # Line 1
        print()   # Line 2
        print()   # Line 3
        print()   # Line 4
        """
    )
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("setvar a 1000")
    d.assert_line_number(2)
    d.sendline("print(a)")
    assert "1000" in d.expect_prompt()
    d.sendline("n")
    d.assert_line_number(3)
    d.sendline("n")
    d.assert_line_number(4)
    d.sendline("back")
    d.assert_line_number(3)
    d.sendline("back")
    d.assert_line_number(2)
    d.sendline("n")
    d.assert_line_number(3)
    d.sendline("print(a)")
    assert "1000" in d.expect_prompt()
    d.quit()


def test_setvar_with_breakpoint():
    d = launch_dejaview(
        """
        a = 1234  # Line 1
        def foo(x):
            b = 567  # Line 3
            return x + b
        print('value:', a) # Line 5
        print('value:', a)
        """
    )
    d.assert_line_number(1)
    d.sendline("b 3")
    d.expect_prompt()
    d.sendline("b 5")
    d.expect_prompt()
    d.sendline("c")
    d.assert_line_number(5)
    d.sendline("print('value:', a)")
    assert "value: 1234" in d.expect_prompt()
    d.sendline("setvar a foo(1)")
    d.expect_prompt()
    d.sendline("print('value:', a)")
    assert "value: 568" in d.expect_prompt()
    d.sendline("n")
    d.assert_line_number(6)
    d.quit()


def test_setvar_field():
    d = launch_dejaview(
        """
        a = lambda: None
        a.x = 123
        print(a.x)
        """
    )
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    d.assert_line_number(3)
    d.sendline("setvar a.x 321")
    d.assert_line_number(3)
    d.sendline("n")
    assert "321" in d.expect_prompt()
    d.quit()


def test_setvar_array():
    d = launch_dejaview(
        """
        a = [1, 2, 3]
        print(a)
        """
    )
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("setvar a[2] 4")
    d.assert_line_number(2)
    d.sendline("n")
    assert "[1, 2, 4]" in d.expect_prompt()
    d.quit()


def test_setvar_array_1():
    d = launch_dejaview(
        """
        a = [1, 2, 3]
        print(a)
        """
    )
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("setvar a [exec('a[2] = 4'), a][1]")
    d.assert_line_number(2)
    d.sendline("n")
    assert "[1, 2, 4]" in d.expect_prompt()
    d.quit()


def test_code_exec_1():
    d = launch_dejaview(
        """
        a = [1, 2, 3]
        print(a)
        print(b) # b will be defined
        print(b)
        """
    )
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("!b = a.pop()")
    d.assert_line_number(2)
    d.sendline("n")
    assert "[1, 2]" in d.expect_prompt()
    d.sendline("!print('value:', b)")
    assert "value: 3" in d.expect_prompt()
    d.sendline("back")
    d.assert_line_number(2)
    d.sendline("back")
    d.assert_line_number(1)
    d.sendline("!print('value:', b)")
    assert "*** NameError: name 'b' is not defined" in d.expect_prompt()
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("!print('value:', b)")
    assert "value: 3" in d.expect_prompt()
    d.sendline("!print('value:', a)")
    assert "value: [1, 2]" in d.expect_prompt()
    d.sendline("!a = [0]")
    d.expect_prompt()
    d.sendline("!('value: ' + str(a.pop()))")
    assert "value: 2" in d.expect_prompt()
    d.sendline("!('value: ' + str(a.pop()))")
    assert "value: 2" in d.expect_prompt()  # Stays the same, pop shouldn't modify
    d.sendline("!print('value:', a)")  # Historical state, should revert back
    assert "value: [1, 2]" in d.expect_prompt()
    d.quit()


def test_code_exec_local_var():
    d = launch_dejaview(
        """
        def foo(arr):
            other = [1, 2, 3]
            other.append(arr[-1])
            print(other)
            return arr
        foo([1, 2])
        """
    )
    d.assert_line_number(1)
    d.sendline("step")
    d.assert_line_number(6)
    d.sendline("step")
    d.assert_line_number(1)
    d.sendline("step")
    d.assert_line_number(2)
    d.sendline("step")
    d.assert_line_number(3)
    d.sendline("!other.append(arr[-1])")
    d.expect_prompt()
    d.sendline("!print(other)")  # Should work normally
    assert "[1, 2, 3, 2]" in d.expect_prompt()
    d.sendline("step")
    d.assert_line_number(4)
    d.sendline("back")
    d.assert_line_number(3)
    d.sendline("!other.append(-1)")  # Should not modify
    d.expect_prompt()
    d.sendline("!print(other)")
    assert "[1, 2, 3, 2]" in d.expect_prompt()
    d.sendline("next")
    d.assert_line_number(4)
    d.sendline("next")
    d.assert_line_number(5)
    d.sendline("!other.append(-1)")  # Should be fine now
    d.expect_prompt()
    d.sendline("!print(other)")
    assert "[1, 2, 3, 2, 2, -1]" in d.expect_prompt()
    d.sendline("next")
    d.assert_line_number(5)
    d.sendline("next")
    d.assert_line_number(6)
    d.sendline("!print(other)")  # Should not exist in this scope
    assert "*** NameError: name 'other' is not defined" in d.expect_prompt()
    d.sendline("!print(1 % 0)")  # Should not crash debugger
    assert "*** ZeroDivisionError: integer modulo by zero" in d.expect_prompt()
    d.quit()
