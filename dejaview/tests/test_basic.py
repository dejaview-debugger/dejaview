from dejaview.tests.util import launch_dejaview


def test_quit():
    d = launch_dejaview(
        """
        print(100 + 1)
        """
    )
    d.expect_prompt()
    d.sendline("quit")
    end = d.expect_end()
    assert "101" not in end
    assert "Traceback" not in end


def test_continue():
    d = launch_dejaview(
        """
        print(100 + 1)
        """
    )
    d.expect_prompt()
    d.sendline("c")
    output = d.expect_prompt()
    assert "101" in output
    assert "The program finished and will be restarted" in output
    d.quit()


def test_step():
    d = launch_dejaview(
        """
        print()  # Line 1
        print()  # Line 2
        print()  # Line 3
        """
    )
    d.assert_line_number(1)
    d.sendline("n")
    d.assert_line_number(2)
    d.sendline("n")
    d.assert_line_number(3)
    d.quit()


def test_breakpoint():
    d = launch_dejaview(
        """
        print()  # Line 1
        breakpoint()
        print()  # Line 3
        """
    )
    d.assert_line_number(1)
    d.sendline("c")
    d.assert_line_number(3)
    d.quit()
