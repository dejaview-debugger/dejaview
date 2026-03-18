import bdb
import os
import pdb
import sys
from dataclasses import dataclass
from types import FrameType
from typing import Any, Callable, Generator, override

import dejaview.counting
import dejaview.patching
import dejaview.snapshots
from dejaview.patching import patching


@dataclass
class StackFrame:
    frame: FrameType | None
    count: int
    being_returned: bool  # are we in the return event of this frame?


@dataclass
class Event:
    count: int  # global count
    stack: list[StackFrame]  # count for each stack frame
    frame: FrameType  # current frame
    event: str  # event type
    arg: Any  # event argument


@dataclass(order=True)
class CounterPositionStack:
    """
    A pattern that matches a CounterPosition by stack count.

    Ordered lexicographically.
    """

    counts: list[int]

    @staticmethod
    def position_key(position: "CounterPosition") -> "CounterPositionStack":
        return position.stack


@dataclass(order=True)
class CounterPositionGlobal:
    """
    A pattern that matches a CounterPosition by global instruction count.

    Ordered by count.
    """

    count: int

    @staticmethod
    def position_key(position: "CounterPosition") -> "CounterPositionGlobal":
        return position.global_


type CounterPositionPattern = CounterPositionStack | CounterPositionGlobal


@dataclass
class CounterPosition:
    """
    Represents a unique position in the execution history.
    The position is defined by the execution count of each frame in the call stack.
    """

    stack: CounterPositionStack
    global_: CounterPositionGlobal

    @staticmethod
    def zero() -> "CounterPosition":
        return CounterPosition(CounterPositionStack([0]), CounterPositionGlobal(0))


class FrameCounter:
    def __init__(self) -> None:
        self.library_prefixes = (
            sys.prefix,  # site-packages
            os.path.dirname(os.__file__),  # standard library
        )
        self.excluded_prefixes = (
            os.path.dirname(dejaview.patching.__file__),
            os.path.dirname(dejaview.counting.__file__),
            os.path.dirname(dejaview.snapshots.__file__),
            "<frozen",
            # "<string>",
        )
        self.debugger_files = (pdb.__file__, bdb.__file__)
        self.own_function_codes = {
            self.settrace.__code__,
            self.breakpointhook.__code__,
            self.__enter__.__code__,
            self.__exit__.__code__,
        }

        self.handlers: list[Callable[[Event], bool]] = []
        self.count = 0
        self.stack = [StackFrame(frame=None, count=0, being_returned=False)]
        self.sub_tracer = None
        self.skipped_frames: list[FrameType] = []
        self.pdb_factory = lambda: self.CustomPdb(self)
        self.pdb: pdb.Pdb | None = None
        self.allow_breakpoints = True

    @property
    def position(self) -> CounterPosition:
        counts = [frame.count for frame in self.stack]
        return CounterPosition(
            CounterPositionStack(counts),
            CounterPositionGlobal(self.count),
        )

    def add_handler_generator(self, handler: Generator[None, Event, None]):
        """
        Handler receives all Event and is removed when the generator finishes.
        """
        next(handler)

        def handler_wrapper(event: Event):
            try:
                handler.send(event)
                return False
            except StopIteration:
                return True

        self.handlers.append(handler_wrapper)

    def add_handler(self, handler: Callable[[Event], bool]):
        """
        Handler receives all Event and is removed if it returns True.
        """
        self.handlers.append(handler)

    def settrace(self, func) -> None:
        self.sub_tracer = func

        # Patch f_trace set by the debugger
        frame: FrameType | None = sys._getframe().f_back
        while frame and frame != self.base_frame:
            if frame.f_trace is not None:
                frame.f_trace = self.get_tracer(frame.f_trace)
            frame = frame.f_back

    def should_skip_frame_recursively(self, frame: FrameType) -> bool:
        """Determine if the frame and all its subframes should be skipped."""
        return (
            frame.f_code in self.own_function_codes
            or frame.f_code.co_filename in self.debugger_files
            or frame.f_code.co_filename.startswith(self.excluded_prefixes)
        )

    def should_skip_frame_non_recursively(self, frame: FrameType) -> bool:
        """Determine if this frame should be skipped."""
        return frame.f_code.co_filename.startswith(self.library_prefixes)

    def get_tracer(self, sub_tracer):
        def tracer(frame: FrameType, event: str, arg: Any) -> Any:
            # Frame filtering only needs to happen on call events: returning None
            # from a call prevents CPython from installing a local tracer, so
            # line/return/exception events for skipped frames are never delivered.
            if event == "call":
                # Skip frames that should be skipped
                while self.skipped_frames:
                    # Is the current frame called by the last skipped frame?
                    if frame.f_back == self.skipped_frames[-1]:
                        self.skipped_frames.append(frame)
                        # Returning None makes us skip the current function
                        # but not calls made from it
                        return None
                    # Otherwise we're done with the last skipped frame so pop it
                    self.skipped_frames.pop()

                if self.should_skip_frame_recursively(frame):
                    self.skipped_frames.append(frame)
                    return None

                if self.should_skip_frame_non_recursively(frame):
                    return None

            # Track the current stack
            if self.stack[-1].being_returned:
                self.stack.pop()  # Pop returned frame on the next event

            # Everything is a global instruction
            self.count += 1

            match event:
                case "call":
                    # Function call has count 0 and first line has count 1
                    self.stack.append(
                        StackFrame(frame=frame, count=0, being_returned=False)
                    )
                case "return":
                    # Mark it as being returned so it gets popped on the next event
                    self.stack[-1].being_returned = True
                    # Returning from function also counts as a instruction in its frame
                    # Note that "return" event also triggers when leaving a frame
                    # due to an unhandled exception
                    self.stack[-1].count += 1
                    # Ensure __return__ is always set for pdb display,
                    # even when pdb tracing is suppressed during replay
                    frame.f_locals["__return__"] = arg
                case "line":
                    self.stack[-1].count += 1
                case "exception":
                    # Also count exceptions
                    self.stack[-1].count += 1
                    # Ensure __exception__ is always set for pdb display,
                    # even when pdb tracing is suppressed during replay
                    frame.f_locals["__exception__"] = (arg[0], arg[1])
                case _:
                    raise ValueError(f"Unknown event type: {event}")

            try:
                # Call the user-defined handlers, remove the ones that return True.
                # Uses index-based iteration so handlers can safely append new
                # handlers during iteration (e.g. _setup_replay_process adding
                # timeline_head_handler after a checkpoint fork).
                i = 0
                while i < len(self.handlers):
                    if self.handlers[i](
                        Event(self.count, self.stack, frame, event, arg)
                    ):
                        self.handlers.pop(i)
                    else:
                        i += 1

                # Call the sub-tracer if it exists
                actual_sub_tracer = self.sub_tracer or sub_tracer
                # self.sub_tracer = None
                if actual_sub_tracer:
                    # Disable patching while calling the sub-tracer to not interfere
                    # with the debugger
                    with patching.set_patching_mode(patching.PatchingMode.OFF):
                        new_tracer = actual_sub_tracer(frame, event, arg)
                    if new_tracer != sub_tracer:
                        self.sub_tracer = new_tracer
                        return self.get_tracer(new_tracer)
                return tracer
            except bdb.BdbQuit:
                # Propagate debugger quit so the outer loop can shut down cleanly
                raise

        return tracer

    def __enter__(self):
        self.backup()
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore the original tracer and settrace
        self.old_settrace(self.old_tracer)
        sys.settrace = self.old_settrace
        sys.breakpointhook = self.old_breakpointhook

    def backup(self):
        self.old_tracer = sys.gettrace()
        self.old_settrace = sys.settrace
        self.old_breakpointhook = sys.breakpointhook
        sys.settrace = self.settrace

    def start(self):
        self.count = 0
        self.sub_tracer = None
        self.base_frame = sys._getframe().f_back
        sys.breakpointhook = self.breakpointhook
        self.old_settrace(self.get_tracer(None))
        return self

    def get_pdb(self):
        if self.pdb is None:
            self.pdb = self.pdb_factory()
        return self.pdb

    def breakpoint(self, frame):
        assert self.allow_breakpoints
        self.get_pdb().set_trace(frame)

    def breakpointhook(self, *args, **kws):
        if self.allow_breakpoints:
            self.breakpoint(sys._getframe().f_back)

    class CustomPdb(pdb.Pdb):
        def __init__(self, counter: "FrameCounter"):
            super().__init__()
            self.counter = counter
            # bdb.Bdb only sets botframe inside run(); initialize it so that
            # set_quit() works even if the program never started (e.g. post-mortem
            # after a startup failure).
            self.botframe = None

        @override
        def trace_dispatch(self, frame, event, arg):
            # When breakpoint pauses are disallowed (e.g. probing for last breakpoint),
            # skip all debugger work
            if self.counter.allow_breakpoints:
                return super().trace_dispatch(frame, event, arg)

        def do_count(self, arg: str):
            for frame in self.counter.stack:
                print(frame.count, frame.frame)
