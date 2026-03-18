import pytest

from dejaview.tests.util import (
    launch_dejaview,
)


@pytest.mark.parametrize(
    "program, should_detect",
    [
        # ===== These cases are detected correctly =====
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            for i in range(30):
                if is_replay():
                    print("replay", i)
                else:
                    print("root", i)
            """,
            True,
            id="if_statement",
        ),
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            for i in range(30):
                print((
                    "replay" if is_replay()
                    else "root"
                ), i)
            """,
            True,
            id="if_expression_different_lines",
        ),
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            for i in range(30 + is_replay() * 30):
                print(i)
            """,
            True,
            id="more_iterations",
        ),
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            for i in range(30 - is_replay() * 30):
                print(i)
            """,
            True,
            id="fewer_iterations",
        ),
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            print([1 for _ in range(30 + is_replay() * 30)])
            """,
            True,
            id="list_comprehension",
        ),
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            funcs = [
                lambda: print("func0"),
                lambda: print("func1"),
            ]
            for i in range(30):
                funcs[is_replay()]()
            """,
            True,
            id="lambdas_different_lines",
        ),
        # ===== These cases are currently not detected, but ideally should be =====
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            for i in range(30):
                print("replay" if is_replay() else "root", i)
            """,
            False,
            id="if_expression_same_line",
        ),
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            funcs = [lambda: print("func0"), lambda: print("func1")]
            for i in range(30):
                funcs[is_replay()]()
            """,
            False,
            id="lambdas_same_line",
        ),
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            for i in range(30):
                print(is_replay(), i)
            """,
            False,
            id="data_flow_only",
        ),
        pytest.param(
            """
            from dejaview.patching.backdoor import is_replay
            funcs = [print, str]
            for i in range(30):
                funcs[is_replay()](i)
            """,
            False,
            id="builtins",
        ),
    ],
)
def test_divergence(program: str, should_detect: bool):
    d = launch_dejaview(program)
    d.assert_line_number(1)
    d.sendline("c")
    assert "restart" in d.expect_prompt()
    d.sendline("c")
    out = d.assert_line_number(1)
    if should_detect:
        assert "Replay divergence detected" in out
        assert "Restarting the debugging session." in out
    else:
        assert "Replay divergence detected" not in out
    d.quit()
