"""
Simple JSON echo server using only the Python standard library.
Shuts down when the request body contains the word "close".

Usage:

    ledit uv run python3 -m dejaview dejaview/tests/programs/http_server.py

    curl -X POST http://localhost:8000 -H "Content-Type: application/json" -d '{"hello": "world"}'
    curl -X POST http://localhost:8000 -H "Content-Type: application/json" -d '{"foo": "close"}'
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class EchoHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except Exception as e:
            print("EXCEPTION:", repr(e))
            import traceback; traceback.print_exc()
            self.send_response(400)
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
            return

        if "close" in json.dumps(data).lower():
            response = json.dumps({"status": "shutting down"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            self.server.running = False
            return

        response = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


if __name__ == "__main__":
    server = HTTPServer(("localhost", 8000), EchoHandler)
    server.running = True
    print("Listening on http://localhost:8000")
    while server.running:
        server.handle_request()
    print("Server closed.")
