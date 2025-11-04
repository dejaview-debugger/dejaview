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
