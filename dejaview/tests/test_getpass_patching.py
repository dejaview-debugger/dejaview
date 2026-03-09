import getpass

from dejaview.tests.util import launch_dejaview


class TestGetpassPatching:
    def test_getpass_memoized_value(self):
        d = launch_dejaview(
            """
            import getpass           # Line 1
            print()                  # Line 2
            print(getpass.getpass()) # Line 3
            print()                  # Line 4
            """
        )

        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)

        # Execute line 3 — getpass prompts for input
        d.sendline("n")
        d.expect_exact("Password: ")
        d.sendline("my_secret")
        out1 = d.assert_line_number(4)

        # Replay line 3 — memoized, no prompt
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("n")
        out2 = d.assert_line_number(4)
        d.quit()

        assert "my_secret" in out1
        assert "my_secret" in out2

    def test_getuser_memoized_value(self):
        d = launch_dejaview(
            """
            import getpass            # Line 1
            print()                   # Line 2
            print(getpass.getuser())  # Line 3
            print()                   # Line 4
            """
        )

        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)

        # Execute line 3
        d.sendline("n")
        out1 = d.assert_line_number(4)

        # Replay line 3 — memoized
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("n")
        out2 = d.assert_line_number(4)
        d.quit()

        username = getpass.getuser()
        assert username in out1
        assert username in out2
