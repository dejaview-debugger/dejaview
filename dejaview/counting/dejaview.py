import bdb
import os
import pdb
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
from dejaview.snapshots.snapshots import (
    DEFAULT_CHECKPOINT_INTERVAL,
    DEFAULT_MAX_CHECKPOINTS,
    SnapshotManager,
)

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
    instruction_count: int = 0  # global instruction count for checkpoint selection

    def decrement(self) -> None:
        """
        Decrement the counter position by one step.
        """
        if self.counts[-1] == 0:
            self.counts.pop()
        else:
            self.counts[-1] -= 1
        self.instruction_count -= 1


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

    stopinfo: DebuggerStopInfo


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
    def __init__(
        self,
        socket_client: Optional[DebugSocketClient] = None,
        checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
        max_checkpoints: int = DEFAULT_MAX_CHECKPOINTS,
    ):
        self.counter = FrameCounter()
        self.snapshot_manager = SnapshotManager[
            ResumeSnapshotArg, ResumeSnapshotReturn
        ](
            checkpoint_interval=checkpoint_interval,
            max_checkpoints=max_checkpoints,
        )
        self.socket_client = socket_client
        self.counter.pdb_factory = lambda: self.CustomPdb(self)
        self.replay_head_reached = False
        self.pending_breakpoints: list[str] = []  # Store breakpoints until pdb is ready
        # self.counter.add_handler(print_handler)

        # Set up checkpoint callback for immediate capture on line events
        self.counter._checkpoint_callback = self._on_instruction

        # Set up command handler immediately if socket is available
        if self.socket_client and self.socket_client.connected:
            self.socket_client.set_command_handler(self._handle_socket_command)

    def _on_instruction(self, count: int) -> None:
        """Called on every line event to check if a checkpoint is needed.

        Captures checkpoints immediately when the interval threshold is reached.
        In replay processes forked from automatic checkpoints, sets up the replay.
        """
        # Only capture checkpoints in root process during normal execution
        if self.snapshot_manager.is_replay_process:
            return

        # Check if we've passed the checkpoint interval threshold
        interval = self.snapshot_manager.checkpoint_interval
        last_count = self.snapshot_manager._last_checkpoint_count

        if count - last_count >= interval:
            # Capture checkpoint immediately
            debug_log(
                f"DEBUG: capturing checkpoint at count={count} (last was {last_count})"
            )
            arg = self.snapshot_manager.capture_snapshot(instruction_count=count)
            if arg is not None:
                # We're in a replay process forked from this checkpoint
                self._setup_replay_process(arg)

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
            debug_log(f"[DejaView] Processing breakpoint: {break_arg}")
            if self.pdb and self.pdb.is_initialized:
                # Forward to pdb's handler which has duplicate checking
                self.pdb._handle_socket_command(command)
            else:
                debug_log(f"[DejaView] Pdb not ready, queueing breakpoint: {break_arg}")
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
            debugger_state=self.serialize_debugger_state(),
            request=request,
        )

        # Calculate target count for checkpoint selection
        target_count: int = 0
        if isinstance(request, ReverseToTargetRequest):
            if request.to is None:
                target_count = 0  # Going to beginning
            else:
                target_count = request.to.instruction_count
        elif isinstance(request, ProbeBreakpointRequest):
            target_count = 0  # Must scan from beginning to find all breakpoints

        return self.snapshot_manager.resume_snapshot(arg, target_count=target_count)

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
                self.apply_stopinfo(request.stopinfo)
                return
            else:
                assert_never(request)

    def get_current_position(self) -> CounterPosition:
        # TODO optimize?
        counts = [frame.count for frame in self.counter.stack]
        return CounterPosition(counts, instruction_count=self.counter.count)

    def serialize_stopinfo(self) -> DebuggerStopInfo:
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
        return stop_info

    def apply_stopinfo(self, info: DebuggerStopInfo) -> None:
        """Apply serialized stop info to the current debugger."""
        pdb_instance = self.get_pdb()

        def index_to_frame(idx: int | None, botframe: Any) -> types.FrameType | None:
            if idx is None:
                return None
            if idx == -1:
                return botframe
            if idx < 0 or idx >= len(self.counter.stack):
                raise ValueError(
                    "Debugger stop info refers to frame index "
                    f"{idx} but stack depth is {len(self.counter.stack)}"
                )
            return self.counter.stack[idx].frame

        # Restore stop info using live frames derived from saved indices.
        botframe = getattr(pdb_instance, "botframe", None)
        stopframe = index_to_frame(info.stop_index, botframe)
        returnframe = index_to_frame(info.return_index, botframe)

        pdb_instance._set_stopinfo(stopframe, returnframe, info.stoplineno)

    def serialize_debugger_state(self) -> DebuggerState:
        pdb_instance = self.get_pdb()

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

        return DebuggerState(
            breakpoints=serialized_breakpoints,
            next_breakpoint_id=bdb.Breakpoint.next,
            breaks=breaks,
            commands=commands,
        )

    def apply_debugger_state(self, state: DebuggerState) -> None:
        """Apply a serialized debugger state to the current debugger."""
        pdb_instance = self.get_pdb()

        # debug_log(f"applying debugger state, {state=}")

        # Clear any stale breakpoint data before applying the snapshot.
        # Also clear in-place to preserve references held by pdb cmdloop
        bdb.Breakpoint.bpbynumber.clear()
        bdb.Breakpoint.bpbynumber.append(None)
        bdb.Breakpoint.bplist.clear()
        bdb.Breakpoint.next = 1
        pdb_instance.breaks.clear()
        pdb_instance.commands.clear()

        # Recreate breakpoints with their original numbers.
        # bdb.Breakpoint.__init__ sets bp.number = Breakpoint.next, then appends
        # to bpbynumber.
        # So for bp.number == index in bpbynumber, we need
        # len(bpbynumber) == bp.number before creation.
        for bp_state in sorted(state.breakpoints, key=lambda bp: bp.number):
            # Pad bpbynumber so that len(bpbynumber) == bp_state.number before creation
            # This ensures the breakpoint will be at the correct index when appended
            while len(bdb.Breakpoint.bpbynumber) < bp_state.number:
                bdb.Breakpoint.bpbynumber.append(None)

            bdb.Breakpoint.next = bp_state.number
            bp = bdb.Breakpoint(
                bp_state.file,
                bp_state.line,
                bp_state.temporary,
                bp_state.cond,
                bp_state.funcname,
            )
            # Now bp is at index bp_state.number in bpbynumber

            bp.enabled = bp_state.enabled
            bp.hits = bp_state.hits
            bp.ignore = bp_state.ignore

        # Pad to ensure length accommodates next_breakpoint_id
        # so that new breakpoints added later will be at the correct index
        while len(bdb.Breakpoint.bpbynumber) < state.next_breakpoint_id:
            bdb.Breakpoint.bpbynumber.append(None)

        bdb.Breakpoint.next = state.next_breakpoint_id
        # Update in-place to preserve references
        pdb_instance.breaks.update(
            {filename: list(lines) for filename, lines in state.breaks.items()}
        )
        pdb_instance.commands.update(
            {num: list(cmds) for num, cmds in state.commands.items()}
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

    def restart(self):
        """
        Restart the debugging session from the beginning.
        """

        request = ReverseToTargetRequest(to=None)
        self.execute_request(request)

    def __enter__(self):
        self.counter.backup()
        self.setup_snapshot()
        self.counter.start()
        self.patches = setup_patching()
        return self

    def timeline_head_handler(
        self, head: CounterPosition
    ) -> Generator[None, Event, None]:
        """
        Track when we reach the timeline head in a replay process.
        When that happens, give control back to the root process.
        """
        while True:
            event = yield
            if event.event != "line":
                continue

            counts = self.get_current_position()
            if counts == head:
                self.replay_head_reached = True
                pdb_instance = self.get_pdb()
                # If pdb would not stop here, immediately hand
                # control back to root before executing past head.
                would_stop = pdb_instance.break_here(event.frame) or (
                    pdb_instance.stop_here(event.frame)
                )
                debug_log(f"reached head in replay process, would_stop={would_stop}")
                if not would_stop:
                    self.forward_request_to_root(
                        ContinueRequest(stopinfo=self.serialize_stopinfo())
                    )
                    assert_never()
                break

    def setup_snapshot(self) -> None:
        # capture initial snapshot at instruction count 0
        arg: ResumeSnapshotArg | None = self.snapshot_manager.capture_snapshot(
            instruction_count=0
        )
        if arg is None:  # we're the root process
            return

        self._setup_replay_process(arg)

    def _setup_replay_process(self, arg: ResumeSnapshotArg) -> None:
        """Set up a replay process after returning from capture_snapshot().

        Installs handlers, deserializes state stores, and applies debugger state.
        Called from both setup_snapshot() (initial checkpoint) and _on_instruction()
        (automatic checkpoints).
        """
        assert self.snapshot_manager.is_replay_process
        debug_log(f"got arg {arg=}, {os.getpid()=}")
        # we're a replay process
        self.replay_head_reached = False
        request = arg.request
        match request:
            case ReverseToTargetRequest():
                pdb_instance = self.get_pdb()
                # Request to pause at beginning
                if request.to is None:
                    # Pause at beginning
                    pdb_instance.set_step()
                    self.counter.add_handler_generator(
                        self.timeline_head_handler(arg.head)
                    )
                # Request to pause at specific position
                else:
                    # Disable all pdb tracing (e.g. breakpoints)
                    self.counter.allow_breakpoints = False
                    # Wait for main doesn't work because it's handled in trace dispatch
                    # so turn it off since we're not stopping until request.to anyway
                    pdb_instance._wait_for_mainpyfile = False

                    # add handler to enter debugger at request.to
                    def handler() -> Generator[None, Event, None]:
                        with patching.SetPatchingMode(patching.PatchingMode.MUTED):
                            while True:
                                event = yield
                                counts = self.get_current_position()
                                if request.to == counts:
                                    self.counter.allow_breakpoints = True
                                    self.counter.breakpoint(event.frame)
                                    break

                        yield from self.timeline_head_handler(arg.head)

                    self.counter.add_handler_generator(handler())

            case ProbeBreakpointRequest():

                def handler() -> Generator[None, Event, None]:
                    with patching.SetPatchingMode(patching.PatchingMode.MUTED):
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

                            if self.get_pdb().break_here(event.frame):
                                last_breakpoint = counts

                self.counter.allow_breakpoints = False
                self.counter.add_handler_generator(handler())

            case _:
                assert_never(request)

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
            else:
                # For control commands, breaks, and clears, we write to the pipe
                debug_log(f"[PDB] Writing command to pipe: {command}")
                if self.stdin_write_fd is not None:
                    try:
                        os.write(self.stdin_write_fd, (command + "\n").encode())
                        debug_log("[PDB] Command written to pipe")
                    except Exception as e:
                        debug_log(f"[PDB] Failed to write to pipe: {e}")

        def do_back(self, arg: str):
            """back

            Rewind the execution by one step.
            """
            self.dejaview.step_back()
            debug_log("returned from step_back")
            return 1

        def do_reverse_continue(self, arg: str):
            """reverse_continue

            Continue execution in reverse until a breakpoint is hit.
            "rc" and "rcontinue" are aliases for "reverse_continue".
            """
            self.dejaview.reverse_continue()
            return 1

        def do_run(self, arg):
            """run

            Rewind the program to the beginning.
            History, breakpoints, actions and debugger options
            are preserved.  "restart" is an alias for "run".
            """
            if arg:
                self.error("DejaView's run command does not take arguments")
                return 0
            raise pdb.Restart

        do_rcontinue = do_reverse_continue
        do_rc = do_reverse_continue
        do_restart = do_run

        def onecmd(self, line):
            stop = super().onecmd(line)
            if (
                self.dejaview.snapshot_manager.is_replay_process
                and self.dejaview.replay_head_reached
                and stop
            ):
                debug_log("at head in replay after command, handing back to root")
                # We are at head in replay and user asked to continue/step; hand back.
                self.dejaview.forward_request_to_root(
                    ContinueRequest(self.dejaview.serialize_stopinfo())
                )
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
                            # Check if breakpoint already exists at this location
                            if ":" in break_arg:
                                file_path, line_str = break_arg.rsplit(":", 1)
                                line_no = int(line_str)
                                canonical = self.canonic(file_path)
                                if (
                                    canonical in self.breaks
                                    and line_no in self.breaks[canonical]
                                ):
                                    debug_log(
                                        f"[PDB] Pending breakpoint already exists at "
                                        f"{canonical}:{line_no}, skipping"
                                    )
                                    continue
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
