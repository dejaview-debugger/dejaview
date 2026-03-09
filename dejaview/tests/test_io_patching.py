import os
import tempfile

from dejaview.tests.util import launch_dejaview


class TestIOPatching:
    def test_os_mkdir_memoized_e2e(self):
        """os.mkdir() is memoized on replay."""
        tmpdir = tempfile.mkdtemp()
        target = os.path.join(tmpdir, "testdir")

        d = launch_dejaview(
            f"""
            import os                           # Line 1
            print()                             # Line 2
            os.mkdir("{target}")                # Line 3
            print(os.path.isdir("{target}"))    # Line 4
            print()                             # Line 5
            """
        )

        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)

        # Execute line 3 — mkdir
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        out1 = d.assert_line_number(5)

        # Step back and replay
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        out2 = d.assert_line_number(5)
        d.quit()

        # Clean up
        os.rmdir(target)
        os.rmdir(tmpdir)

        assert "True" in out1
        assert "True" in out2

    def test_os_open_close_memoized_e2e(self):
        """os.open() and os.close() are memoized on replay."""
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()

        d = launch_dejaview(
            f"""
            import os                                                # Line 1
            print()                                                  # Line 2
            fd = os.open("{tmp.name}", os.O_RDONLY)                  # Line 3
            print(fd)                                                # Line 4
            os.close(fd)                                             # Line 5
            print()                                                  # Line 6
            """
        )

        d.assert_line_number(1)
        d.sendline("n")
        d.assert_line_number(2)
        d.sendline("n")
        d.assert_line_number(3)

        # Execute line 3 — os.open
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        out1 = d.assert_line_number(5)

        # Step back and replay
        d.sendline("back")
        d.assert_line_number(4)
        d.sendline("back")
        d.assert_line_number(3)
        d.sendline("n")
        d.assert_line_number(4)
        d.sendline("n")
        out2 = d.assert_line_number(5)
        d.quit()

        os.unlink(tmp.name)

        # Same fd should be printed both times
        assert out1.strip() != ""
        assert out1 == out2
