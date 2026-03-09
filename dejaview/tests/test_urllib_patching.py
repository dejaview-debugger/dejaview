import urllib.request
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from dejaview.patching.custom_patchers import UrlopenPatcher
from dejaview.patching.patching import (
    Patches,
    capture,
    capture_funcs,
    reset,
    reset_funcs,
)
from dejaview.patching.state_store import FunctionStateStore, StateStore


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


@pytest.fixture
def local_server():
    """Start a tiny HTTP server that returns a known response."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"test body")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestUrllibPatching:
    def test_urlopen_memoized(self, local_server):
        """urlopen() returns stored response despite different URL on replay."""
        p = Patches()
        p.patch(urllib.request, "urlopen", UrlopenPatcher)

        snap = capture()
        resp = urllib.request.urlopen(local_server + "/play")
        body = resp.read()

        reset(snap)
        # Different URL — memoized response should still be returned
        replay_resp = urllib.request.urlopen(local_server + "/replay")
        replay_body = replay_resp.read()

        assert body == replay_body == b"test body"

        p.__exit__(None, None, None)
