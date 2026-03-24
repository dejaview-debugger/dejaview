import bdb
import io
import json
import os
import pdb
import sys
import traceback
import types
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from random import randbytes
from typing import (
    Any,
    NoReturn,
    TextIO,
    assert_never,
    cast,
)

from dejaview.counting.counting import (
    CounterPosition,
    CounterPositionGlobal,
    CounterPositionPattern,
    CounterPositionStack,
    Event,
    FrameCounter,
)
from dejaview.counting.error_detection import (
    StreamErrorDetector,
    StreamMismatchError,
    VerifyMode,
)
from dejaview.counting.socket_client import DebugSocketClient
from dejaview.patching import backdoor, patching
from dejaview.patching.setup import setup_patching
from dejaview.patching.state_store import StateStore
from dejaview.snapshots.safe_fork import safe_fork
from dejaview.snapshots.snapshots import (
    DEFAULT_MAX_SNAPSHOTS,
    DEFAULT_SNAPSHOT_INTERVAL,
    SnapshotManager,
)

original_print = print  # save original print so we don't use the patched version

# Debug mode flag.
DEBUG = False


def debug_log(*args: Any) -> None:
    """Print debug messages to stderr if DEBUG is enabled."""
    if DEBUG:
        original_print(*args, file=sys.stderr, flush=True)


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

    breakpoints: list[BreakpointState]
    next_breakpoint_id: int
    breaks: dict[str, list[int]]
    commands: dict[int, list[str]]
    # Sorted list of (counts_key, statements) pairs in execution order.
    # Each entry maps a CounterPosition to a list of source strings.
    # Populated by default() so replay processes can re-execute them.
    exec_history: list[tuple[tuple, list[str]]] = field(default_factory=list)


@dataclass
class ReverseToTargetRequest:
    """
    Reverse to the given counter position.
    """

    to: CounterPositionPattern | None  # None means we go to the beginning


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
    error_detector_reference: VerifyMode

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
        *,
        socket_client: DebugSocketClient | None = None,
        snapshot_interval: int = DEFAULT_SNAPSHOT_INTERVAL,
        max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
        is_testing: bool = False,
    ):
        """
        Args:
            socket_client: Optional client for receiving commands and sending output.
            is_testing:
                Are we running the DejaView test suite? If so, configure parameters to
                be more test-friendly (e.g. shorter error detection period).
        """
        self.counter = FrameCounter()
        self.snapshot_manager = SnapshotManager[
            ResumeSnapshotArg, ResumeSnapshotReturn
        ](
            snapshot_interval=snapshot_interval,
            max_snapshots=max_snapshots,
        )
        self.error_detector = StreamErrorDetector(
            salt=randbytes(16),
            # Increase frequency of error checks in testing since test code is short
            period=2 if is_testing else StreamErrorDetector.DEFAULT_PERIOD,
        )
        self.socket_client = socket_client
        self.is_testing = is_testing
        self.counter.pdb_factory = lambda: self.CustomPdb(self)
        # Head position for replay processes
        self.replay_head_reached = False
        # Store breakpoints until pdb is ready
        self.pending_breakpoints: list[str] = []
        # True when viewing historical state (replay in progress)
        self.replay_active = False
        # Sorted list of (counts_key, statements) pairs in execution order.
        # Records every user statement so replay processes can re-execute them.
        self._exec_history: list[tuple[tuple, list[str]]] = []
        # self.counter.add_handler(print_handler)

        # Register snapshot handler to capture snapshots at interval
        self.counter.add_handler(self._on_instruction)

        # Set up command handler immediately if socket is available
        if self.socket_client and self.socket_client.connected:
            self.socket_client.set_command_handler(self._handle_socket_command)

        self.patches: patching.Patches | None = None

    def _on_instruction(self, event: Event) -> bool:
        """Handler that checks if a snapshot is needed on each line event.

        Captures snapshots immediately when the interval threshold is reached.
        In replay processes forked from automatic snapshots, sets up the replay.

        Returns False always (this handler is never removed).
        """
        if event.event != "line":
            return False

        # Only capture snapshots in root process during normal execution
        if self.snapshot_manager.is_replay_process:
            return False

        # Check if we've passed the snapshot interval threshold
        count = event.count
        interval = self.snapshot_manager.snapshot_interval
        last_count = self.snapshot_manager._last_snapshot_count

        if count - last_count >= interval:
            # Capture snapshot immediately
            debug_log(
                f"DEBUG: capturing snapshot at count={count} (last was {last_count})"
            )
            arg = self.snapshot_manager.capture_snapshot(position=self.counter.position)
            if arg is not None:
                # We're in a replay process forked from this snapshot
                self._setup_replay_process(arg)

        return False

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
            break_arg = command.removeprefix("break ")
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
            head=self.counter.position,
            debugger_state=self.serialize_debugger_state(),
            error_detector_reference=self.error_detector.as_verify_mode(),
            request=request,
        )
        self.replay_active = True
        try:
            # Calculate target for snapshot selection
            if isinstance(request, ReverseToTargetRequest) and request.to is not None:
                target_count = request.to
            else:
                target_count = CounterPosition.zero().global_
            return self.snapshot_manager.resume_snapshot(arg, target_count=target_count)
        finally:
            self.replay_active = False

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
                to_counts = probe_result.to_counts
                pattern = to_counts.global_ if to_counts is not None else None
                request = ReverseToTargetRequest(to=pattern)
            elif isinstance(request, QuitRequest):
                pdb_instance = self.get_pdb()
                pdb_instance.do_quit("")
                return
            elif isinstance(request, ContinueRequest):
                self.apply_stopinfo(request.stopinfo)
                return
            else:
                assert_never(request)

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

        serialized_breakpoints: list[BreakpointState] = []
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
            exec_history=list(self._exec_history),
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
        # Sync exec history back to the root's authoritative store
        self._exec_history = list(state.exec_history)

    def _register_exec_replay_handlers(
        self, exec_history: list[tuple[tuple, list[str]]]
    ) -> None:
        """
        Register a counter handler that re-executes user statements during replay.
        """
        if not exec_history:
            return

        stack = [(pos_key, list(stmts)) for pos_key, stmts in exec_history]
        idx = [0]

        def handler(event: Event) -> bool:
            if event.event != "line":
                return False
            if idx[0] >= len(stack):
                return True

            pos_key, statements = stack[idx[0]]
            if tuple(self.counter.position.stack.counts) != pos_key:
                return False

            frame = event.frame
            local_vars = frame.f_locals
            for source in statements:
                try:
                    exec(  # noqa: S102
                        compile(source + "\n", "<stdin>", "single"),
                        frame.f_globals,
                        local_vars,
                    )
                except Exception:
                    pass  # Statement may have been a failed expression
            debug_log(f"[DejaView] Replayed {len(statements)} exec(s) at {pos_key}")

            idx[0] += 1
            if idx[0] >= len(stack):
                return True
            return False

        self.counter.add_handler(handler)

    def reverse_step(self):
        pos = self.counter.position.global_
        # Go to the previous instruction globally
        if pos.count <= 1:
            print("reverse_step reached the beginning")
            return
        pattern = CounterPositionGlobal(pos.count - 1)
        request = ReverseToTargetRequest(to=pattern)
        self.execute_request(request)

    def reverse_next(self):
        pos = self.counter.position.stack
        counts = list(pos.counts)
        if counts[-1] == 0:
            if len(counts) <= 2:
                print("reverse_next reached the beginning")
                return
            counts.pop()
        else:
            counts[-1] -= 1
        pattern = CounterPositionStack(counts)
        request = ReverseToTargetRequest(to=pattern)
        self.execute_request(request)

    def reverse_return(self):
        pos = self.counter.position.stack
        if len(pos.counts) <= 2:
            print("reverse_return reached the beginning")
            return
        pattern = CounterPositionStack(pos.counts[:-1])
        request = ReverseToTargetRequest(to=pattern)
        self.execute_request(request)

    def reverse_continue(self):
        """
        Reverse-continue to the most recent breakpoint before the current position.
        """

        request = ReverseContinueRequest(before=self.counter.position)
        self.execute_request(request)

    def restart(self):
        """
        Restart the debugging session from the beginning.
        """

        request = ReverseToTargetRequest(to=None)
        self.execute_request(request)

    @contextmanager
    def patching_context(self):
        if not self.patches:
            self.patches = setup_patching()
            try:
                with self.patches:
                    yield
            finally:
                self.patches = None
        else:
            yield

    @contextmanager
    def context(self):
        assert patching.get_patching_mode() == patching.PatchingMode.OFF
        with self.patching_context(), self.counter:
            # Register error_detection_handler before snapshot to make sure it runs
            # before the timeline_head_handler, so that assert_no_remaining_reference
            # sees a fully up-to-date error detector.
            self.counter.add_handler(self.error_detection_handler)
            self.setup_snapshot()
            with patching.set_patching_mode(patching.PatchingMode.NORMAL):
                yield

    def summarize_event(self, event: Event) -> bytearray | bytes:
        data = bytearray()
        data.extend(event.event.encode())  # event type
        data.extend(event.frame.f_lineno.to_bytes(4))  # line number
        if event.event == "call":
            f_code = event.frame.f_code
            data.extend(f_code.co_filename.encode())  # filename

        # Human readable debug version
        # data = (
        #     f"{event.event=} "
        #     f"{event.frame.f_code.co_filename}:{event.frame.f_lineno + 1}"
        # ).encode()
        # if event.event == "exception":
        #     exc_type, exc_value, exc_traceback = event.arg
        #     data += f" exc={exc_type.__name__}: {exc_value}".encode()
        return data

    def error_detection_handler(self, event: Event) -> bool:
        if DEBUG:
            next_count = self.error_detector._count + 1
            checkpoint = (
                "checkpoint"
                if next_count % self.error_detector._period == 0
                else "event"
            )
            mode = "replay" if self.snapshot_manager.is_replay_process else "root"
            debug_log(
                "[ERRDET]"
                f" mode={mode}"
                f" {checkpoint}"
                f" next_count={next_count}"
                f" event={event.event}"
                f" file={event.frame.f_code.co_filename}"
                f" line={event.frame.f_lineno}"
                f" patch_mode={patching.get_patching_mode().name}"
            )
        self.error_detector.update(self.summarize_event(event))
        return False

    def timeline_head_handler(
        self, head: CounterPosition
    ) -> Generator[None, Event, None]:
        """
        Track when we reach the timeline head in a replay process.
        When that happens, give control back to the root process.
        """
        while True:
            event = yield
            counts = self.counter.position
            global_eq = counts.global_ == head.global_
            stack_eq = counts.stack == head.stack
            if global_eq != stack_eq:
                raise StreamMismatchError(
                    count=counts.global_.count,
                    expected=None,
                    actual=None,
                    message="Head position is inconsistent\n"
                    f"in replay: {counts}\n"
                    f"in root: {head}\n",
                )
            if global_eq:
                self.error_detector.assert_no_remaining_reference()
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
            position=CounterPosition.zero()
        )
        if arg is None:  # we're the root process
            return

        self._setup_replay_process(arg)

    def _setup_replay_process(self, arg: ResumeSnapshotArg) -> None:
        """Set up a replay process after returning from capture_snapshot().

        Installs handlers, deserializes state stores, and applies debugger state.
        Called from both setup_snapshot() (initial snapshot) and _on_instruction()
        (automatic snapshots).
        """
        assert self.snapshot_manager.is_replay_process
        debug_log(f"got arg {arg=}, {os.getpid()=}")
        backdoor._is_replay = True  # Let tests know we're a replay process
        self.replay_head_reached = False
        self.error_detector.switch_to_verify(arg.error_detector_reference)
        request = arg.request
        match request:
            case ReverseToTargetRequest(to=target):
                pdb_instance = self.get_pdb()
                # Request to pause at beginning
                if target is None:
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
                    # so turn it off since we're not stopping until target anyway
                    pdb_instance._wait_for_mainpyfile = False

                    # add handler to enter debugger at target
                    def handler() -> Generator[None, Event, None]:
                        with patching.set_patching_mode(patching.PatchingMode.MUTED):
                            while True:
                                event = yield
                                position = self.counter.position
                                if target.position_key(position) == target:
                                    self.counter.allow_breakpoints = True
                                    self.counter.breakpoint(event.frame)
                                    break

                        yield from self.timeline_head_handler(arg.head)

                    self.counter.add_handler_generator(handler())

            case ProbeBreakpointRequest():

                def handler() -> Generator[None, Event, None]:
                    with patching.set_patching_mode(patching.PatchingMode.MUTED):
                        last_breakpoint: CounterPosition | None = None

                        while True:
                            event = yield
                            counts = self.counter.position
                            if counts == request.before:
                                target = last_breakpoint
                                self.snapshot_manager.return_from_replay(
                                    ResumeSnapshotReturn(LastBreakpointResult(target))
                                )
                                assert_never()

                            if event.event != "line":
                                continue

                            if self.get_pdb().break_here(event.frame):
                                last_breakpoint = counts

                self.counter.allow_breakpoints = False
                self.counter.add_handler_generator(handler())

            case _:
                assert_never(request)

        # set function state stores
        StateStore.deserialize(arg.function_states)
        self.apply_debugger_state(arg.debugger_state)
        self._register_exec_replay_handlers(arg.debugger_state.exec_history)

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
            # Tracks a just-emitted call stop so user_line does not emit it again.
            self._pending_call_stop: tuple[str, int] | None = None

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
            query_commands = {
                "locals()": ("locals()", "active_locals", self.do_locals),
                "where": ("where", "active_where", self.do_where),
            }
            query_command = query_commands.get(command)
            if query_command is not None:
                display_name, pipe_command, handler = query_command

                # Route through pipe during replay so the active process handles it.
                if self.dejaview.replay_active:
                    debug_log(
                        f"[PDB] Routing {display_name} through pipe to replay process"
                    )
                    if self.stdin_write_fd is not None:
                        try:
                            os.write(self.stdin_write_fd, f"{pipe_command}\n".encode())
                            debug_log(f"[PDB] {pipe_command} written to pipe")
                        except Exception as e:
                            debug_log(f"[PDB] Failed to write to pipe: {e}")
                else:
                    try:
                        debug_log(f"[PDB] Executing {display_name} directly")
                        handler("")
                    except Exception as e:
                        if self.socket_client:
                            error_msg = (
                                f"Error executing {display_name}: "
                                f"{e}\n{traceback.format_exc()}"
                            )
                            self.socket_client.send_output(error_msg, "stderr")
            elif command.startswith("setvar "):
                try:
                    # Extract the argument string: "name value"
                    setvar_arg = command.removeprefix("setvar ")
                    debug_log(f"[PDB] Setting variable: {setvar_arg}")
                    self.do_setvar(setvar_arg)
                except Exception as e:
                    debug_log(
                        f"[PDB] Failed to set variable: {e}\n{traceback.format_exc()}"
                    )
                    if self.socket_client:
                        self.socket_client.send_response(
                            f"setvar {setvar_arg}", {"success": False, "error": str(e)}
                        )
            else:
                # For control commands, breaks, and clears, we write to the pipe
                debug_log(f"[PDB] Writing command to pipe: {command}")
                if self.stdin_write_fd is not None:
                    try:
                        os.write(self.stdin_write_fd, (command + "\n").encode())
                        debug_log("[PDB] Command written to pipe")
                    except Exception as e:
                        debug_log(f"[PDB] Failed to write to pipe: {e}")

        def do_reverse_step(self, arg: str):
            """reverse_step

            Reverse to the previous line, stop at the first possible occasion
            (either in a function that was called or in the current function).
            "rs" and "rstep" are aliases for "reverse_step".
            """
            self.dejaview.reverse_step()
            debug_log("returned from reverse_step")
            return 1

        def do_reverse_next(self, arg: str):
            """reverse_next

            Reverse execution until the previous line in the current function
            is reached or the beginning of the function is reached.
            "rn", "rnext" and "back" are aliases for "reverse_next".
            """
            self.dejaview.reverse_next()
            debug_log("returned from reverse_next")
            return 1

        def do_reverse_return(self, arg: str):
            """reverse_return

            Reverse execution until the calling line of the current function.
            "rr" and "rreturn" are aliases for "reverse_return".
            """
            self.dejaview.reverse_return()
            debug_log("returned from reverse_return")
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

        do_back = do_reverse_next
        do_rstep = do_reverse_step
        do_rs = do_reverse_step
        do_rnext = do_reverse_next
        do_rn = do_reverse_next
        do_rreturn = do_reverse_return
        do_rr = do_reverse_return
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
                    ContinueRequest(stopinfo=self.dejaview.serialize_stopinfo())
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
                current_location = (frame.f_code.co_filename, int(frame.f_lineno))
                if self._pending_call_stop == current_location:
                    debug_log(
                        "[PDB] Skipping duplicate user_line stopped event "
                        f"for call-stop location {current_location[0]}:"
                        f"{current_location[1]}"
                    )
                    self._pending_call_stop = None
                else:
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

            if self.socket_client and self.socket_client.connected:
                filename = frame.f_code.co_filename
                lineno = int(frame.f_lineno)
                self._pending_call_stop = (filename, lineno)
                debug_log(
                    f"[PDB] user_call #{self.user_call_count} "
                    f"(id={self.instance_id}): Sending stopped event with "
                    f"lineno = {lineno}"
                )
                self.socket_client.send_stopped_with_location("step", filename, lineno)

            # Continue with pdb handling after notifying the adapter.
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

                def is_internal_frame(filename: str) -> bool:
                    """True for runtime/debugger internals we don't want in VS Code."""
                    if filename.startswith("<frozen "):
                        return True
                    base = os.path.basename(filename)
                    return base in {"pdb.py", "bdb.py", "cmd.py", "runpy.py"}

                def make_source(filename: str) -> dict[str, str]:
                    # Synthetic names like <string> are not real file paths.
                    if filename.startswith("<") and filename.endswith(">"):
                        return {"name": filename}
                    return {
                        "path": filename,
                        "name": os.path.basename(filename),
                    }

                # Call setup() first to refresh self.stack with current state
                # This is essential after stepping back, as self.stack needs
                # to be updated
                if self.curframe:
                    tb = getattr(self, "tb", None)
                    # In post-mortem mode, preserving traceback keeps the
                    # user's failing frame in the stack.
                    self.setup(self.curframe, tb)

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

                    # Filter only true internals; keep user and harness frames.
                    if is_internal_frame(filename):
                        continue

                    stack_frames.append(
                        {
                            "id": index,
                            "name": function_name,
                            "source": make_source(filename),
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

        def do_active_locals(self, arg: str):
            """Alias for do_locals, used when routing through pipe during replay."""
            return self.do_locals(arg)

        def do_active_where(self, arg: str):
            """Alias for do_where, used when routing through pipe during replay."""
            return self.do_where(arg)

        def _execute_ephemeral(self, stmt: str) -> None:
            """Execute stmt in a forked child; parent (replay) state is unchanged.

            Child captures stdout, executes the statement, writes result JSON
            to a pipe, then exits. Parent reads result and sends output to the
            adapter via socket_client. Used when is_replay_process=True.
            """
            if not self.curframe:
                return

            warning = (
                "Warning: You are in a replay process. This statement is executed "
                "in an ephemeral replay sandbox. Changes are not stored for "
                "future replays and will be reverted."
            )
            self.error(warning)

            read_fd, write_fd = os.pipe()
            try:
                pid = safe_fork()
            except Exception:
                os.close(read_fd)
                os.close(write_fd)
                raise

            if pid == 0:
                # CHILD: execute, write result to pipe, exit
                os.close(read_fd)
                result: dict
                try:
                    captured = io.StringIO()
                    old_stdout = sys.stdout
                    sys.stdout = captured
                    frame = self.curframe
                    local_vars = frame.f_locals
                    try:
                        with patching.set_patching_mode(patching.PatchingMode.NORMAL):
                            exec(  # noqa: S102
                                compile(stmt + "\n", "<stdin>", "single"),
                                frame.f_globals,
                                local_vars,
                            )
                    finally:
                        sys.stdout = old_stdout
                    result = {"success": True, "output": captured.getvalue()}
                except Exception as exc:
                    result = {
                        "success": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                raw = (json.dumps(result) + "\n").encode()
                os.write(write_fd, raw)
                os.close(write_fd)
                os._exit(0)
            else:
                # PARENT: read result from child, wait, output to terminal + socket
                os.close(write_fd)
                chunks = []
                while True:
                    chunk = os.read(read_fd, 4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                os.close(read_fd)
                os.waitpid(pid, 0)

                raw_result = b"".join(chunks).decode("utf-8").strip()
                try:
                    result = json.loads(raw_result)
                except Exception as exc:
                    result = {
                        "success": False,
                        "error_type": "Error",
                        "error": f"Failed to parse child result: {exc}",
                    }

                if result.get("success"):
                    output = result.get("output", "")
                    if output:
                        # Write to pdb's stdout (terminal) so pexpect tests see it
                        self.stdout.write(output)
                        self.stdout.flush()
                        if self.socket_client and self.socket_client.connected:
                            self.socket_client.send_output(output, "stdout")
                else:
                    error = result.get("error", "Unknown error")
                    error_type = result.get("error_type", "")
                    # Format like pdb's self.error(): "*** ExcType: message"
                    formatted = f"{error_type}: {error}" if error_type else error
                    self.error(formatted)
                    if self.socket_client and self.socket_client.connected:
                        self.socket_client.send_output(f"*** {formatted}\n", "stderr")

        def default(self, line):
            """Handle unrecognized commands as Python statements.

            When at the head (not viewing historical state), the statement
            is recorded so replay processes can re-execute it.
            """
            # Record for replay if at head
            if (
                not self.dejaview.snapshot_manager.is_replay_process
                and not self.dejaview.replay_active
                and self.curframe
            ):
                # Strip leading '!' which PDB uses to force exec
                stmt = line.removeprefix("!")
                pos_key = tuple(self.dejaview.counter.position.stack.counts)
                history = self.dejaview._exec_history
                if history and history[-1][0] == pos_key:
                    history[-1][1].append(stmt)
                else:
                    history.append((pos_key, [stmt]))
                debug_log(f"[PDB] Recorded statement for replay: {stmt}")

            if self.dejaview.snapshot_manager.is_replay_process:
                self._execute_ephemeral(stmt=line.removeprefix("!"))
                return

            super().default(line)

        def do_setvar(self, arg: str):
            """setvar <name> <value>

            Set a variable in the current frame's local scope.
            """
            if not self.curframe:
                raise ValueError("No current frame")

            # Parse the argument string into var_name and var_value
            parts = arg.split(" ", 1)
            if len(parts) != 2:
                self.error("Usage: setvar <name> <value>")
                return
            var_name, var_value = parts

            # Reject while viewing historical state
            if (
                self.dejaview.snapshot_manager.is_replay_process
                or self.dejaview.replay_active
            ):
                error_msg = (
                    "Cannot modify variables while viewing a historical state. "
                    "Continue to the current execution point first."
                )
                debug_log("[PDB] setvar rejected: viewing historical state")
                if self.socket_client and self.socket_client.connected:
                    self.socket_client.send_response(
                        f"setvar {var_name} {var_value}",
                        {"success": False, "error": error_msg},
                    )
                self.error(error_msg)
                return

            # Delegate to default() which handles exec + recording for replay
            stmt = f"{var_name} = {var_value}"
            self.default(f"!{stmt}")

            debug_log(f"[PDB] Set {var_name} via exec")

            if self.socket_client and self.socket_client.connected:
                # Read back the value to send in the response
                try:
                    value = eval(  # noqa: S307
                        var_name, self.curframe.f_globals, self.curframe.f_locals
                    )
                    self.socket_client.send_response(
                        f"setvar {var_name} {var_value}",
                        {
                            "success": True,
                            "value": repr(value),
                            "valueType": type(value).__name__,
                        },
                    )
                except Exception as e:
                    self.socket_client.send_response(
                        f"setvar {var_name} {var_value}",
                        {"success": False, "error": str(e)},
                    )


def print_handler(event: Event):
    code = event.frame.f_code
    func_name = code.co_name
    file = code.co_filename
    line_no = event.frame.f_lineno
    print(f"#{event.count} {event.event} {func_name}() at line {line_no} of {file}")
