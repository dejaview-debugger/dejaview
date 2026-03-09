import socket

from dejaview.tests.util import launch_dejaview


class TestSocketPatching:
    def test_gethostname_memoized_e2e(self):
        d = launch_dejaview(
            """
            import socket              # Line 1
            print()                    # Line 2
            print(socket.gethostname())  # Line 3
            print()                    # Line 4
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

        hostname = socket.gethostname()
        assert hostname in out1
        assert hostname in out2

    def test_gethostbyname_memoized_e2e(self):
        d = launch_dejaview(
            """
            import socket                            # Line 1
            print()                                  # Line 2
            print(socket.gethostbyname("localhost"))  # Line 3
            print()                                  # Line 4
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

        expected = socket.gethostbyname("localhost")
        assert expected in out1
        assert expected in out2
