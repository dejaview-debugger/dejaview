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
        d.run_to(3)

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
        d.run_to(3)

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

    def test_socket_identity_preserved_e2e(self):
        """Socket object preserves family/type/proto across replay."""
        d = launch_dejaview(
            """
            import socket                                          # Line 1
            print()                                                # Line 2
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # Line 3
            print(s.family, s.type, s.proto)                       # Line 4
            s.close()                                              # Line 5
            print()                                                # Line 6
            """
        )

        d.assert_line_number(1)
        d.run_to(4)

        # Execute line 4 — prints family/type/proto
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

        # family=2 (AF_INET), type=1 (SOCK_STREAM), proto=0
        assert "2 1 0" in out1
        assert out1 == out2

    def test_socket_getaddrinfo_memoized_e2e(self):
        """socket.getaddrinfo is memoized across replay."""
        d = launch_dejaview(
            """
            import socket                                    # Line 1
            print()                                          # Line 2
            info = socket.getaddrinfo("localhost", 80)       # Line 3
            print(info[0][4])                                # Line 4
            print()                                          # Line 5
            """
        )

        d.assert_line_number(1)
        d.run_to(4)

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

        assert out1 == out2
