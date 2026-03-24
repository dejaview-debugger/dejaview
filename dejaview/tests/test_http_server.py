"""End-to-end test for HTTP server replay under DejaView."""

import time
import urllib.request
from threading import Thread

from dejaview.tests.util import launch_dejaview


def _send_requests(port: int) -> None:
    """Send test requests to the HTTP server, retrying until it's ready."""
    for _ in range(50):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}",
                data=b"hello",
                method="POST",
            )
            urllib.request.urlopen(req)
            break
        except (ConnectionRefusedError, urllib.error.URLError):
            time.sleep(0.1)
    else:
        raise RuntimeError(f"Could not connect to port {port}")

    req2 = urllib.request.Request(
        f"http://127.0.0.1:{port}",
        data=b"close",
        method="POST",
    )
    urllib.request.urlopen(req2)


class TestHttpServerReplay:
    def test_http_server_replays(self):
        """An HTTP server program replays identically after restart."""
        d = launch_dejaview(
            """
            from http.server import BaseHTTPRequestHandler, HTTPServer


            class EchoHandler(BaseHTTPRequestHandler):
                def do_POST(self):
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)

                    if body == b"close":
                        self.server.running = False

                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)


            server = HTTPServer(("127.0.0.1", 0), EchoHandler)
            port = server.server_address[1]
            server.running = True
            print(f"PORT:{port}")
            while server.running:
                server.handle_request()
            print("SERVER_DONE")
            """,
            timeout=30,
            snapshot_interval=1000000,
            stress_test=True,
        )

        # First run: extract port, send requests
        d.expect_prompt()
        d.sendline("c")

        # Safe to use expect here — first run is deterministic
        d.expect("PORT:(\\d+)")
        port = int(d.match.group(1))

        sender = Thread(target=_send_requests, args=(port,), daemon=True)
        sender.start()

        out = d.expect_prompt()
        assert "SERVER_DONE" in out, f"Server did not complete:\n{out}"

        # Second run (replay)
        d.sendline("c")
        out = d.expect_prompt()
        assert "Replay divergence" not in out, f"Divergence detected:\n{out}"
        assert "post mortem" not in out, f"Post mortem:\n{out}"
        assert "SERVER_DONE" in out, f"Replay did not complete:\n{out}"

        d.quit()
