import json
import socket
import sys
import threading
from collections.abc import Callable
from typing import Any

from dejaview.patching.patching import PatchingMode, set_patching_mode

# Debug mode flag - set to False to disable debug logging
DEBUG = False


def debug_log(message: str) -> None:
    """Print debug messages to stderr if DEBUG is enabled."""
    if DEBUG:
        print(message, file=sys.stderr, flush=True)


class DebugSocketClient:
    """
    Socket client for communicating with the VS Code debug adapter.
    Sends debugger events and receives commands via TCP.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5678):
        self.host = host
        self.port = port
        self.socket: socket.socket | None = None
        self.connected = False
        self.command_handler: Callable[[str], None] | None = None
        self.receive_thread: threading.Thread | None = None
        self.running = False

    @set_patching_mode(PatchingMode.OFF)
    def connect(self) -> bool:
        """Connect to the debug adapter server."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            self.running = True

            # Start receiving thread
            self.receive_thread = threading.Thread(
                target=self._receive_loop, daemon=True
            )
            self.receive_thread.start()

            return True
        except Exception as e:
            debug_log(f"Failed to connect to debug adapter: {e}")
            return False

    @set_patching_mode(PatchingMode.OFF)
    def disconnect(self):
        """Disconnect from the debug adapter."""
        self.running = False
        self.connected = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

    def set_command_handler(self, handler: Callable[[str], None]):
        """Set a callback function to handle incoming commands."""
        self.command_handler = handler

    @set_patching_mode(PatchingMode.OFF)
    def _receive_loop(self):
        """Background thread to receive commands from the debug adapter."""
        buffer = ""
        while self.running and self.socket:
            try:
                data = self.socket.recv(4096)
                if not data:
                    break

                buffer += data.decode("utf-8")

                # Process complete messages (delimited by newlines)
                while "\n" in buffer:
                    message, buffer = buffer.split("\n", 1)
                    if message.strip():
                        self._handle_message(message)
            except Exception as e:
                if self.running:
                    debug_log(f"Error receiving data: {e}")
                break

        self.connected = False

    def _handle_message(self, message: str):
        """Handle an incoming message from the debug adapter."""
        debug_log(f"[SOCKET] Received message: {message}")
        try:
            data = json.loads(message)
            command = data.get("command", "")
            debug_log(f"[SOCKET] Parsed command: {command}")
            debug_log(f"[SOCKET] command_handler is: {self.command_handler}")

            if self.command_handler and command:
                debug_log(f"[SOCKET] Calling command_handler with: {command}")
                self.command_handler(command)
            else:
                debug_log(
                    f"[SOCKET] Not calling handler "
                    f"(handler={self.command_handler}, command={command})"
                )
        except json.JSONDecodeError as e:
            debug_log(f"[SOCKET] Failed to parse message: {message}, error: {e}")

    def send_output(self, content: str, category: str = "console"):
        """Send output text to the debug console."""
        self._send_message({"type": "output", "content": content, "category": category})

    def send_stopped(self, reason: str = "step", thread_id: int = 1):
        """Notify the debug adapter that execution has stopped."""
        self._send_message({"type": "stopped", "reason": reason, "threadId": thread_id})

    def send_stopped_with_location(
        self, reason: str, filename: str, lineno: int, thread_id: int = 1
    ):
        """Notify the debug adapter that execution has stopped,
        including current location.
        """
        self._send_message(
            {
                "type": "stopped",
                "reason": reason,
                "threadId": thread_id,
                "filename": filename,
                "lineno": lineno,
            }
        )

    def send_continued(self, thread_id: int = 1):
        """Notify the debug adapter that execution has continued."""
        self._send_message({"type": "continued", "threadId": thread_id})

    def send_terminated(self):
        """Notify the debug adapter that the program has terminated."""
        self._send_message({"type": "terminated"})

    def send_response(self, command: str, data: dict[str, Any]):
        """Send a response to a command."""
        message = {"type": "response", "command": command, **data}
        self._send_message(message)

    @set_patching_mode(PatchingMode.OFF)
    def _send_message(self, data: dict[str, Any]):
        """Send a JSON message to the debug adapter."""
        if not self.connected or not self.socket:
            return

        try:
            message = json.dumps(data) + "\n"
            self.socket.sendall(message.encode("utf-8"))
        except Exception as e:
            debug_log(f"Failed to send message: {e}")
            self.connected = False
