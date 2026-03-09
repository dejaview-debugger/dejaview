import tempfile

from dejaview.tests.util import launch_dejaview


class TestTempfilePatching:
    def test_mkdtemp_memoized_e2e(self):
        d = launch_dejaview(
            """
            import tempfile              # Line 1
            print()                      # Line 2
            print(tempfile.mkdtemp())    # Line 3
            print()                      # Line 4
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

        assert "/tmp/" in out1
        assert "/tmp/" in out2

    def test_gettempdir_memoized_e2e(self):
        d = launch_dejaview(
            """
            import tempfile                # Line 1
            print()                        # Line 2
            print(tempfile.gettempdir())   # Line 3
            print()                        # Line 4
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

        expected = tempfile.gettempdir()
        assert expected in out1
        assert expected in out2
