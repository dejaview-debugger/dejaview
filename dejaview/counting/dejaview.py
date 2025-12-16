import os
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Generator, List, Optional, TextIO, cast

from dejaview.counting.counting import Event, FrameCounter
from dejaview.counting.socket_client import DebugSocketClient
from dejaview.patching import patching
from dejaview.patching.setup import setup_patching
from dejaview.patching.state_store import StateStore
from dejaview.snapshots.snapshots import SnapshotManager

# Debug mode flag - set to False to disable debug logging
DEBUG = False


def debug_log(message: str) -> None:
    """Print debug messages to stderr if DEBUG is enabled."""
    if DEBUG:
        print(message, file=sys.stderr, flush=True)


class _SafeStdin:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def readline(self, *args, **kwargs):
        try:
            return self._stream.readline(*args, **kwargs)
        except ValueError as exc:
            if exc.args and exc.args[0] == "I/O operation on closed file.":
                return ""
            raise

    def __getattr__(self, name):
        return getattr(self._stream, name)


@dataclass
class State:
    to_counts: List[int]
    function_states: Any
    """Should be equivalent to get_type_hints(StateStore.serialize()).get('return')"""
    # TODO: add debugger state


class DejaView:
    def __init__(self, socket_client: Optional[DebugSocketClient] = None):
        self.counter = FrameCounter()
        self.snapshot_manager = SnapshotManager()
        self.socket_client = socket_client
        self.counter.pdb_factory = lambda: self.CustomPdb(self)
        self.pending_breakpoints: list[str] = []  # Store breakpoints until pdb is ready
        # self.counter.add_handler(print_handler)

        # Set up command handler immediately if socket is available
        if self.socket_client and self.socket_client.connected:
            self.socket_client.set_command_handler(self._handle_socket_command)

    def _handle_socket_command(self, command: str):
        """Handle commands at the DejaView level, before pdb is ready."""
        debug_log(f"[DejaView] Received command: {command}")

        # Queue breakpoint commands until pdb is ready
        if command.startswith("break "):
            break_arg = command[6:]  # Remove "break " prefix
            debug_log(f"[DejaView] Queueing breakpoint: {break_arg}")
            self.pending_breakpoints.append(break_arg)
        else:
            # For other commands, try to pass to pdb if it exists
            pdb = self.counter.pdb
            if pdb and hasattr(pdb, "_handle_socket_command"):
                debug_log(f"[DejaView] Forwarding to pdb: {command}")
                pdb._handle_socket_command(command)
            else:
                debug_log(f"[DejaView] Pdb not ready, ignoring command: {command}")

    def step_back(self):
        counts = [frame.count for frame in self.counter.stack]
        if counts[-1] == 0:
            counts.pop()
        else:
            counts[-1] -= 1
        state = State(counts, StateStore.serialize())
        self.snapshot_manager.resume_snapshot(state)

    def __enter__(self):
        self.counter.backup()
        self.setup_snapshot()
        self.counter.start()
        self.patches = setup_patching()
        return self

    def setup_snapshot(self) -> None:
        # capture snapshot
        state: State = self.snapshot_manager.capture_snapshot()
        if state is not None:  # if we're resuming from a snapshot
            # add handler to enter debugger at to_count
            def handler() -> Generator[None, Event, None]:
                with patching.SetPatchingMode(patching.PatchingMode.MUTED):
                    while True:
                        event = yield
                        # TODO optimize
                        counts = [frame.count for frame in event.stack]
                        if state.to_counts == counts:
                            self.counter.allow_breakpoints = True
                            # print(
                            #     "enter breakpoint after stepping back to count",
                            #     state.to_count,
                            # )
                            self.counter.breakpoint(event.frame)
                            break

            self.counter.allow_breakpoints = False
            self.counter.add_handler_generator(handler())

            # set function state stores
            StateStore.deserialize(state.function_states)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.patches.__exit__(exc_type, exc_val, exc_tb)
        self.counter.__exit__(exc_type, exc_val, exc_tb)

    def get_pdb(self):
        return self.counter.get_pdb()

    class CustomPdb(FrameCounter.CustomPdb):
        def __init__(self, dejaview: "DejaView"):
            super().__init__(dejaview.counter)
            self.dejaview = dejaview
            self.use_rawinput = False

            self.socket_client = dejaview.socket_client
            self.stdin_read_fd = None
            self.stdin_write_fd = None
            self.original_stdin = None
            self.is_initialized = False

            # For debugging / metrics purposes
            self.user_line_call_count = 0
            self.user_call_count = 0
            self.instance_id = id(self)

            # Initialize the private backing variables for the properties
            self._last_stopped_frame = None
            self._last_stopped_lineno = None

            # Set up command handler if socket is available
            if self.socket_client and self.socket_client.connected:
                # Create a pipe for stdin redirection
                self.stdin_read_fd, self.stdin_write_fd = os.pipe()
                self.original_stdin = sys.stdin
                # Redirect stdin to our pipe
                sys.stdin = os.fdopen(self.stdin_read_fd, "r")
                self.stdin = cast(TextIO, _SafeStdin(sys.stdin))
                debug_log("[PDB] Stdin redirected to pipe")
            else:
                # Wrap it in SafeStdin regardless of whether sockets are used
                self.stdin = cast(TextIO, _SafeStdin(sys.stdin))

        def __setattr__(self, name, value):
            if DEBUG and name == "_last_stopped_lineno":
                debug_log(f"[PDB] __setattr__: Setting _last_stopped_lineno to {value}")
                for line in traceback.format_stack()[-4:-1]:
                    debug_log(f"  {line.strip()}")
            super().__setattr__(name, value)

        @property
        def last_stopped_lineno(self):
            value = self._last_stopped_lineno
            if DEBUG:
                debug_log(
                    f"[PDB] Reading last_stopped_lineno on object {id(self)}, "
                    f"value = {value}"
                )
            return value

        @last_stopped_lineno.setter
        def last_stopped_lineno(self, value):
            if DEBUG:
                debug_log(f"[PDB] Setting last_stopped_lineno to {value}, stack:")
                for line in traceback.format_stack()[-4:-1]:
                    debug_log(f"  {line.strip()}")
            self._last_stopped_lineno = value

        @property
        def last_stopped_frame(self):
            return self._last_stopped_frame

        @last_stopped_frame.setter
        def last_stopped_frame(self, value):
            self._last_stopped_frame = value

        def _handle_socket_command(self, command: str):
            """Handle commands received from the socket (called from socket thread)."""
            debug_log(f"[PDB] Received socket command: {command}")

            # Special handling for query commands that can be executed immediately
            if command == "locals()":
                try:
                    debug_log("[PDB] Executing locals() directly")
                    self.do_locals("")
                except Exception as e:
                    if self.socket_client:
                        error_msg = (
                            f"Error executing locals(): {e}\n{traceback.format_exc()}"
                        )
                        self.socket_client.send_output(error_msg, "stderr")
            elif command == "where":
                try:
                    debug_log("[PDB] Executing where directly")
                    self.do_where("")
                except Exception as e:
                    if self.socket_client:
                        error_msg = (
                            f"Error executing where: {e}\n{traceback.format_exc()}"
                        )
                        self.socket_client.send_output(error_msg, "stderr")
            elif command.startswith("break "):
                try:
                    # Extract the break argument (e.g., "file.py:line")
                    break_arg = command[6:]  # Remove "break " prefix
                    debug_log(f"[PDB] Setting breakpoint: {break_arg}")
                    self.do_break(break_arg)
                    debug_log(f"[PDB] Breakpoint set: {break_arg}")
                except Exception as e:
                    debug_log(
                        f"[PDB] Failed to set breakpoint: {e}\n{traceback.format_exc()}"
                    )
                    if self.socket_client:
                        error_msg = (
                            f"Error setting breakpoint: {e}\n{traceback.format_exc()}"
                        )
                        self.socket_client.send_output(error_msg, "stderr")
            else:
                # For control commands, write to the pipe
                debug_log(f"[PDB] Writing control command to pipe: {command}")
                if self.stdin_write_fd is not None:
                    try:
                        os.write(self.stdin_write_fd, (command + "\n").encode())
                        debug_log("[PDB] Command written to pipe")
                    except Exception as e:
                        debug_log(f"[PDB] Failed to write to pipe: {e}")

        def do_back(self, arg: str):
            self.quitting = True
            self.dejaview.step_back()

        def stop_here(self, frame):
            return self.counter.allow_breakpoints and super().stop_here(frame)

        def user_line(self, frame):
            """Called when we stop or break at a line."""
            self.user_line_call_count += 1
            debug_log(
                f"[PDB] user_line #{self.user_line_call_count} called at "
                f"{frame.f_code.co_filename}:{frame.f_lineno}"
            )
            debug_log(
                f"[PDB] user_line: self.curindex = "
                f"{self.curindex if hasattr(self, 'curindex') else 'Not set'}"
            )

            # Set pending breakpoints now that pdb is initialized
            if not self.is_initialized:
                self.is_initialized = True
                if self.dejaview.pending_breakpoints:
                    debug_log(
                        f"[PDB] Setting "
                        f"{len(self.dejaview.pending_breakpoints)} "
                        f"pending breakpoints"
                    )
                    for break_arg in self.dejaview.pending_breakpoints:
                        try:
                            debug_log(f"[PDB] Setting pending breakpoint: {break_arg}")
                            self.do_break(break_arg)
                        except Exception as e:
                            debug_log(
                                f"[PDB] Failed to set pending breakpoint "
                                f"{break_arg}: {e}\n{traceback.format_exc()}"
                            )
                    self.dejaview.pending_breakpoints.clear()

            # Send stopped event BEFORE calling super() which blocks in cmdloop
            if self.socket_client and self.socket_client.connected:
                # Capture the state RIGHT NOW before anything else happens
                self.last_stopped_frame = frame
                # Convert to int() to ensure we get the value
                self.last_stopped_lineno = int(frame.f_lineno)

                # Send the line number directly in the stopped event so
                # VS Code knows where we are. This avoids race conditions
                # with querying the stack later
                debug_log(
                    f"[PDB] user_line #{self.user_line_call_count} "
                    f"(id={self.instance_id}): Sending stopped event with "
                    f"lineno = {frame.f_lineno}"
                )
                self.socket_client.send_stopped_with_location(
                    "step", frame.f_code.co_filename, frame.f_lineno
                )
            else:
                debug_log("[PDB] Socket not connected, cannot send stopped event")
            # Now call super which will block in cmdloop
            super().user_line(frame)

        def user_call(self, frame, argument_list):
            """Called when stepping into a function."""
            self.user_call_count += 1
            debug_log(
                f"[PDB] user_call #{self.user_call_count} called at "
                f"{frame.f_code.co_filename}:{frame.f_lineno}"
            )

            # Save the frame where we stopped
            self.last_stopped_frame = frame
            self.last_stopped_lineno = frame.f_lineno
            debug_log(
                f"[PDB] user_call #{self.user_call_count}: "
                f"Set last_stopped_lineno = {self.last_stopped_lineno}"
            )

            # Don't send stopped event here, wait for user_line
            # which is called right after
            super().user_call(frame, argument_list)

        def user_return(self, frame, return_value):
            """Called when a return trap is set here."""
            if self.socket_client and self.socket_client.connected:
                self.socket_client.send_stopped("step")
            super().user_return(frame, return_value)

        def user_exception(self, frame, exc_info):
            """Called when we stop on an exception."""
            if self.socket_client and self.socket_client.connected:
                self.socket_client.send_stopped("exception")
            super().user_exception(frame, exc_info)

        def do_where(self, arg: str):
            """Override where to send stack trace via socket."""
            if self.socket_client and self.socket_client.connected:
                stack_frames = []
                index = 0

                # Call setup() first to refresh self.stack with current state
                # This is essential after stepping back, as self.stack needs
                # to be updated
                if self.curframe:
                    self.setup(self.curframe, None)

                debug_log(
                    f"[PDB] do_where: After setup(), self.stack has "
                    f"{len(self.stack)} frames"
                )
                debug_log(f"[PDB] do_where: curindex = {self.curindex}")

                # Use pdb's internal stack which is correctly maintained
                # self.stack is a list of (frame, lineno) tuples where
                # lineno is the correct historical line
                for i, (frame, lineno) in enumerate(self.stack):
                    debug_log(
                        f"[PDB]   Stack[{i}]: {frame.f_code.co_filename}:{lineno}"
                    )

                # Build the stack frames for VS Code
                for i, (frame, lineno) in enumerate(self.stack):
                    code = frame.f_code
                    filename = code.co_filename
                    function_name = code.co_name

                    # Filter out debugger-internal frames, but allow test programs
                    if filename == "<string>":
                        continue

                    # Allow test programs
                    if "/tests/" in filename or "\\tests\\" in filename:
                        pass  # Include test programs
                    # Block pdb internals and dejaview implementation
                    elif (
                        filename.endswith("pdb.py")
                        or filename.endswith("bdb.py")
                        or filename.endswith("cmd.py")
                        or "dejaview" in filename
                    ):
                        continue

                    stack_frames.append(
                        {
                            "id": index,
                            "name": function_name,
                            "source": {
                                "path": filename,
                                "name": os.path.basename(filename),
                            },
                            "line": lineno,
                            "column": 0,
                        }
                    )
                    index += 1

                # Reverse the stack frames so the current frame is first (index 0)
                # VS Code expects innermost frame first
                stack_frames.reverse()
                # Re-assign IDs after reversing
                for i, stack_frame_dict in enumerate(stack_frames):
                    stack_frame_dict["id"] = i

                self.socket_client.send_response("where", {"stackFrames": stack_frames})
            else:
                return super().do_where(arg)

        def do_locals(self, arg: str):
            """Override locals() to send variables via socket."""
            if self.socket_client and self.socket_client.connected:
                variables = {}
                if self.curframe:
                    local_vars = self.curframe.f_locals
                    for key, value in local_vars.items():
                        try:
                            variables[key] = repr(value)
                        except Exception:
                            variables[key] = "<unavailable>"

                self.socket_client.send_response("locals()", {"variables": variables})
            else:
                # Fallback: print locals to stdout
                if self.curframe:
                    self.message(str(self.curframe.f_locals))


def print_handler(event: Event):
    code = event.frame.f_code
    func_name = code.co_name
    file = code.co_filename
    line_no = event.frame.f_lineno
    print(f"#{event.count} {event.event} {func_name}() at line {line_no} of {file}")
