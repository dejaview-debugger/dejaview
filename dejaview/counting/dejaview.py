import bdb
import os
import sys
import traceback
import types
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    Generator,
    List,
    NoReturn,
    Optional,
    TextIO,
    assert_never,
    cast,
)

from dejaview.counting.counting import Event, FrameCounter
from dejaview.counting.socket_client import DebugSocketClient
from dejaview.patching import patching
from dejaview.patching.setup import setup_patching
from dejaview.patching.state_store import StateStore
from dejaview.snapshots.snapshots import SnapshotManager

# Debug mode flag - set to False to disable debug logging
DEBUG = False


def debug_log(*args: Any) -> None:
    """Print debug messages to stderr if DEBUG is enabled."""
    if DEBUG:
        print(*args, file=sys.stderr, flush=True)


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
class CounterPosition:
    """
    Represents a unique position in the execution history.
    The position is defined by the execution count of each frame in the call stack.
    """

    counts: List[int]

    def decrement(self) -> None:
        """
        Decrement the counter position by one step.
        """
        if self.counts[-1] == 0:
            self.counts.pop()
        else:
            self.counts[-1] -= 1


@dataclass
class BreakpointState:
    """Serializable snapshot of a single breakpoint."""

    number: int
    file: str
    line: int
    cond: str | None
    funcname: str | None
    enabled: bool
    temporary: bool
    hits: int
    ignore: int


@dataclass
class DebuggerStopInfo:
    """
    Information about where the debugger should stop next.
    """

    stoplineno: int
    stop_index: int | None  # index into FrameCounter.stack, -1 for botframe
    return_index: int | None  # index into FrameCounter.stack, -1 for botframe


@dataclass
class DebuggerState:
    """Serializable snapshot of debugger state that must survive forks."""

    breakpoints: List[BreakpointState]
    next_breakpoint_id: int
    breaks: Dict[str, List[int]]
    commands: Dict[int, List[str]]
    stop_info: DebuggerStopInfo | None


@dataclass
class ReverseToTargetRequest:
    """
    Reverse to the given counter position.
    """

    to: CounterPosition | None  # None means we go to the beginning


@dataclass
class ProbeBreakpointRequest:
    """
    Find the last breakpoint before the given position.
    """

    before: CounterPosition


@dataclass
class ReverseContinueRequest:
    """
    Go to last breakpoint before the given position.
    """

    before: CounterPosition


@dataclass
class ContinueRequest:
    """
    Request to continue execution in the root process, extending the timeline.
    """

    pass


@dataclass
class QuitRequest:
    """
    Request to terminate the debugging session.
    """

    pass


RequestForReplay = ReverseToTargetRequest | ProbeBreakpointRequest
RequestForRootOnly = ContinueRequest | ReverseContinueRequest | QuitRequest
RequestForRoot = RequestForReplay | RequestForRootOnly


@dataclass
class LastBreakpointResult:
    """
    Result of a ProbeBreakpointRequest.
    Contains the position of the last hit breakpoint, or None if none was found.
    """

    to_counts: CounterPosition | None


# Is a return value, not an action
ValueResult = LastBreakpointResult


@dataclass
class NextActionResult:
    """
    Indicates that the replay process ended with an action for the root process
    to handle.
    """

    request: RequestForRoot
    debugger_state: DebuggerState


@dataclass
class ResumeSnapshotArg:
    """
    Arguments passed from the root process to a new replay process.
    """

    function_states: Any
    """Should be equivalent to get_type_hints(StateStore.serialize()).get('return')"""

    head: CounterPosition  # the current position of the root process
    debugger_state: DebuggerState

    request: RequestForReplay


@dataclass
class ResumeSnapshotReturn:
    """
    The result returned from a replay process to the root process.
    """

    result: ValueResult | NextActionResult


class DejaView:
    def __init__(self, socket_client: Optional[DebugSocketClient] = None):
        self.counter = FrameCounter()
        self.snapshot_manager = SnapshotManager[
            ResumeSnapshotArg, ResumeSnapshotReturn
        ]()
        self.socket_client = socket_client
        self.counter.pdb_factory = lambda: self.CustomPdb(self)
        self.replay_head_reached = False
        self.pending_breakpoints: list[str] = []  # Store breakpoints until pdb is ready
        # self.counter.add_handler(print_handler)

        # Set up command handler immediately if socket is available
        if self.socket_client and self.socket_client.connected:
            self.socket_client.set_command_handler(self._handle_socket_command)

    @property
    def pdb(self) -> "DejaView.CustomPdb | None":
        pdb = self.counter.pdb
        assert isinstance(pdb, DejaView.CustomPdb | None)
        return pdb

    def _handle_socket_command(self, command: str):
        """Handle commands at the DejaView level, before pdb is ready."""
        debug_log(f"[DejaView] Received command: {command}")

        # Queue breakpoint commands until pdb is ready
        if command.startswith("break "):
            break_arg = command[6:]  # Remove "break " prefix
            debug_log(f"[DejaView] Queueing breakpoint: {break_arg}")
            if self.pdb and self.pdb.is_initialized:
                debug_log(f"[DejaView] Pdb is ready, setting breakpoint: {break_arg}")
                try:
                    self.pdb.do_break(break_arg)
                except Exception as e:
                    debug_log(
                        f"[DejaView] Failed to set breakpoint immediately: "
                        f"{e}\n{traceback.format_exc()}"
                    )
            else:
                self.pending_breakpoints.append(break_arg)
        else:
            # For other commands, try to pass to pdb if it exists
            pdb = self.pdb
            if pdb:
                debug_log(f"[DejaView] Forwarding to pdb: {command}")
                pdb._handle_socket_command(command)
            else:
                debug_log(f"[DejaView] Pdb not ready, ignoring command: {command}")

    def execute_request_for_replay(
        self, request: RequestForReplay
    ) -> ResumeSnapshotReturn:
        """
        Execute a request in a new replay process and return the result.
        """
        assert not self.snapshot_manager.is_replay_process, (
            "cannot be called in replay process"
        )
        arg = ResumeSnapshotArg(
            function_states=StateStore.serialize(),
            head=self.get_current_position(),
            debugger_state=self.serialize_debugger_state(include_stopinfo=False),
            request=request,
        )
        return self.snapshot_manager.resume_snapshot(arg)

    def execute_request_with_return(self, request: RequestForReplay) -> ValueResult:
        ret = self.execute_request_for_replay(request)
        result = ret.result
        debug_log(f"execute_request_with_return got {result=}")
        assert isinstance(result, ValueResult)
        return result

    def forward_request_to_root(self, request: RequestForRoot) -> NoReturn:
        """
        Execute a request in the root process.
        """
        assert self.snapshot_manager.is_replay_process, (
            "can only be called in replay process"
        )
        self.snapshot_manager.return_from_replay(
            ResumeSnapshotReturn(
                NextActionResult(request, self.serialize_debugger_state())
            )
        )

    def execute_request(self, request: RequestForRoot):
        debug_log(f"execute_request, {request=}")

        if self.snapshot_manager.is_replay_process:
            self.forward_request_to_root(request)
            assert_never()

        while True:
            if isinstance(request, RequestForReplay):
                ret = self.execute_request_for_replay(request)
                result = ret.result
                assert isinstance(result, NextActionResult)
                self.apply_debugger_state(result.debugger_state)
                request = result.request
            elif isinstance(request, ReverseContinueRequest):
                probe_result = self.execute_request_with_return(
                    ProbeBreakpointRequest(before=request.before)
                )
                request = ReverseToTargetRequest(to=probe_result.to_counts)
            elif isinstance(request, QuitRequest):
                pdb_instance = self.get_pdb()
                pdb_instance.do_quit("")
                return
            elif isinstance(request, ContinueRequest):
                return
            else:
                assert_never(request)

    def get_current_position(self) -> CounterPosition:
        counts = [frame.count for frame in self.counter.stack]
        return CounterPosition(counts)

    def serialize_debugger_state(self, include_stopinfo: bool = True) -> DebuggerState:
        pdb_instance = self.get_pdb()

        def frame_to_index(frame: Any, botframe: Any) -> int | None:
            if frame is None:
                return None
            for idx, sf in enumerate(self.counter.stack):
                if sf.frame is frame:
                    return idx
            if frame is botframe:
                return -1
            raise ValueError("Frame not found in FrameCounter stack")

        serialized_breakpoints: List[BreakpointState] = []
        for bp in bdb.Breakpoint.bpbynumber:
            if bp is None:
                continue
            serialized_breakpoints.append(
                BreakpointState(
                    number=bp.number,
                    file=bp.file,
                    line=bp.line,
                    cond=bp.cond,
                    funcname=bp.funcname,
                    enabled=bp.enabled,
                    temporary=bp.temporary,
                    hits=bp.hits,
                    ignore=bp.ignore,
                )
            )

        breaks = {
            filename: list(lines) for filename, lines in pdb_instance.breaks.items()
        }
        commands = {num: list(cmds) for num, cmds in pdb_instance.commands.items()}
        stop_info: DebuggerStopInfo | None
        if include_stopinfo:
            botframe = getattr(pdb_instance, "botframe", None)
            stop_info = DebuggerStopInfo(
                stoplineno=getattr(pdb_instance, "stoplineno", -1),
                stop_index=frame_to_index(
                    getattr(pdb_instance, "stopframe", None), botframe
                ),
                return_index=frame_to_index(
                    getattr(pdb_instance, "returnframe", None), botframe
                ),
            )
        else:
            stop_info = None

        return DebuggerState(
            breakpoints=serialized_breakpoints,
            next_breakpoint_id=bdb.Breakpoint.next,
            breaks=breaks,
            commands=commands,
            stop_info=stop_info,
        )

    def apply_debugger_state(self, state: DebuggerState) -> None:
        """Apply a serialized debugger state to the current debugger."""
        pdb_instance = self.get_pdb()

        # debug_log(f"applying debugger state, {state=}")

        def index_to_frame(idx: int | None, botframe: Any) -> types.FrameType | None:
            if idx is None:
                return None
            if idx == -1:
                return botframe
            if idx < 0 or idx >= len(self.counter.stack):
                raise ValueError(
                    "Debugger state refers to frame index "
                    f"{idx} but stack depth is {len(self.counter.stack)}"
                )
            return self.counter.stack[idx].frame

        # Clear any stale breakpoint data before applying the snapshot.
        bdb.Breakpoint.bpbynumber = [None]
        bdb.Breakpoint.bplist = {}
        bdb.Breakpoint.next = 1
        pdb_instance.breaks = {}
        pdb_instance.commands = {}

        # Recreate breakpoints with their original numbers.
        for bp_state in sorted(state.breakpoints, key=lambda bp: bp.number):
            bdb.Breakpoint.next = bp_state.number
            bp = bdb.Breakpoint(
                bp_state.file,
                bp_state.line,
                bp_state.temporary,
                bp_state.cond,
                bp_state.funcname,
            )
            bp.enabled = bp_state.enabled
            bp.hits = bp_state.hits
            bp.ignore = bp_state.ignore

        bdb.Breakpoint.next = state.next_breakpoint_id
        pdb_instance.breaks = {
            filename: list(lines) for filename, lines in state.breaks.items()
        }
        pdb_instance.commands = {
            num: list(cmds) for num, cmds in state.commands.items()
        }

        # Restore stop info using live frames derived from saved indices.
        if state.stop_info is not None:
            botframe = getattr(pdb_instance, "botframe", None)
            stopframe = index_to_frame(state.stop_info.stop_index, botframe)
            returnframe = index_to_frame(state.stop_info.return_index, botframe)

            pdb_instance._set_stopinfo(
                stopframe, returnframe, state.stop_info.stoplineno
            )

    def step_back(self):
        pos = self.get_current_position()
        pos.decrement()
        request = ReverseToTargetRequest(to=pos)
        self.execute_request(request)

    def reverse_continue(self):
        """
        Reverse-continue to the most recent breakpoint before the current position.
        """

        request = ReverseContinueRequest(before=self.get_current_position())
        self.execute_request(request)

    def __enter__(self):
        self.counter.backup()
        self.setup_snapshot()
        self.counter.start()
        self.patches = setup_patching()
        return self

    def setup_snapshot(self) -> None:
        # capture snapshot
        arg: ResumeSnapshotArg | None = self.snapshot_manager.capture_snapshot()
        if arg is None:  # we're the root process
            return

        assert self.snapshot_manager.is_replay_process
        debug_log(f"got arg {arg=}, {os.getpid()=}")
        # we're a replay process
        self.replay_head_reached = False
        request = arg.request
        match request:
            case ReverseToTargetRequest():
                # add handler to enter debugger at to_count
                def handler() -> Generator[None, Event, None]:
                    with patching.SetPatchingMode(patching.PatchingMode.MUTED):
                        while True:
                            event = yield
                            # TODO optimize?
                            counts = self.get_current_position()
                            if request.to is None or request.to == counts:
                                self.counter.allow_breakpoints = True
                                self.counter.breakpoint(event.frame)
                                break

                    # Track when we reach the recorded head so the next continue-like
                    # command can hand control back to root.
                    while True:
                        event = yield
                        if event.event != "line":
                            continue

                        counts = self.get_current_position()
                        if counts == arg.head:
                            self.replay_head_reached = True
                            pdb_instance = self.counter.get_pdb()
                            # If pdb would not stop here, immediately hand
                            # control back to root before executing past head.
                            would_stop = pdb_instance.break_here(event.frame) or (
                                pdb_instance.stop_here(event.frame)
                            )
                            debug_log(
                                "reached head in replay process, "
                                f"would_stop={would_stop}"
                            )
                            if not would_stop:
                                self.forward_request_to_root(ContinueRequest())
                                assert_never()
                            break

                self.counter.allow_breakpoints = False
                self.counter.add_handler_generator(handler())
            case ProbeBreakpointRequest():

                def handler() -> Generator[None, Event, None]:
                    with patching.SetPatchingMode(patching.PatchingMode.MUTED):
                        pdb_instance = self.counter.get_pdb()
                        last_breakpoint: CounterPosition | None = None

                        while True:
                            event = yield
                            if event.event != "line":
                                continue

                            counts = self.get_current_position()
                            if counts == request.before:
                                target = last_breakpoint
                                self.snapshot_manager.return_from_replay(
                                    ResumeSnapshotReturn(LastBreakpointResult(target))
                                )
                                assert_never()

                            if pdb_instance.break_here(event.frame):
                                last_breakpoint = counts

                self.counter.allow_breakpoints = False
                self.counter.add_handler_generator(handler())

        # set function state stores
        StateStore.deserialize(arg.function_states)
        self.apply_debugger_state(arg.debugger_state)

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
            self.dejaview.step_back()
            debug_log("returned from step_back")
            return 1

        def do_reverse_continue(self, arg: str):
            self.dejaview.reverse_continue()
            return 1

        do_rcontinue = do_reverse_continue
        do_rc = do_reverse_continue

        def onecmd(self, line):
            stop = super().onecmd(line)
            if (
                self.dejaview.snapshot_manager.is_replay_process
                and self.dejaview.replay_head_reached
                and stop
            ):
                debug_log("at head in replay after command, handing back to root")
                # We are at head in replay and user asked to continue/step; hand back.
                self.dejaview.forward_request_to_root(ContinueRequest())
            return stop

        def set_quit(self):
            if self.dejaview.snapshot_manager.is_replay_process:
                self.dejaview.forward_request_to_root(QuitRequest())
            else:
                super().set_quit()

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
