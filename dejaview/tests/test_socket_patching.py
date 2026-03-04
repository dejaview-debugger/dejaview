import socket
from collections import defaultdict

import pytest

from dejaview.patching.patching import (
    Patches,
    capture,
    capture_funcs,
    reset,
    reset_funcs,
)
from dejaview.patching.state_store import FunctionStateStore, StateStore
from dejaview.tests.util import launch_dejaview


@pytest.fixture(autouse=True)
def _clean_global_state():
    old_capture = list(capture_funcs)
    old_reset = list(reset_funcs)
    old_store = StateStore.store

    StateStore.store = defaultdict(FunctionStateStore)

    yield

    capture_funcs.clear()
    capture_funcs.extend(old_capture)
    reset_funcs.clear()
    reset_funcs.extend(old_reset)
    StateStore.store = old_store


class TestSocketPatching:
    @pytest.mark.parametrize(
        "func_name, play_args, replay_args",
        [
            ("gethostbyname", ("localhost",), ("example.com",)),
            ("getaddrinfo", ("localhost", 80), ("example.com", 443)),
            ("gethostname", (), ()),
        ],
    )
    def test_module_func_diverges_without_replay(
        self, monkeypatch, func_name, play_args, replay_args
    ):
        """Memoized replay vs. fresh call produce different results."""
        call_count = 0

        def _fake(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return f"result{call_count}"

        monkeypatch.setattr(f"socket.{func_name}", _fake)

        p = Patches()
        p.patch(socket, func_name)

        snap = capture()
        first = getattr(socket, func_name)(*play_args)
        assert first == "result1"

        # Replay: reset and call again → memoized value
        reset(snap)
        replayed = getattr(socket, func_name)(*replay_args)

        # Fresh: call again without resetting → new value
        fresh = getattr(socket, func_name)(*play_args)

        assert replayed == "result1", "replay should return the stored value"
        assert fresh == "result2", "without replay, a new value is produced"
        assert replayed != fresh, "memoized replay and fresh call differ"

        p.__exit__(None, None, None)

    def test_instance_method_diverges_without_replay(self, monkeypatch):
        """Memoized replay vs. fresh call for a socket instance method."""
        call_count = 0

        def _fake_getsockname(self):
            nonlocal call_count
            call_count += 1
            return ("0.0.0.0", call_count)

        monkeypatch.setattr(socket.socket, "getsockname", _fake_getsockname)

        p = Patches()
        p.patch(socket.socket, "getsockname")

        sock = socket.socket.__new__(socket.socket)

        snap = capture()
        first = sock.getsockname()
        assert first == ("0.0.0.0", 1)

        reset(snap)
        replayed = sock.getsockname()

        fresh = sock.getsockname()

        assert replayed == ("0.0.0.0", 1), "replay should return the stored value"
        assert fresh == ("0.0.0.0", 2), "without replay, a new value is produced"
        assert replayed != fresh, "memoized replay and fresh call differ"

        p.__exit__(None, None, None)

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
