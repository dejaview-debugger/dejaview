import os
import sys
import types
import pdb
import bdb
import typing
from dataclasses import dataclass

from ..patching import patching


@dataclass
class Event:
    count: int
    frame: types.FrameType
    event: str
    arg: any


class FrameCounter:
    def __init__(self):
        self.excluded_prefixes = (
            sys.prefix,
            os.path.dirname(os.__file__),
            os.path.dirname(patching.__file__),
            "<frozen",
            "<string>",
        )
        self.debugger_files = (pdb.__file__, bdb.__file__)
        self.own_function_codes = {
            self.settrace.__code__,
            self.breakpointhook.__code__,
            self.__enter__.__code__,
            self.__exit__.__code__,
        }

        self.handlers = []
        self.count = 0
        self.sub_tracer = None
        self.skipped_frames = []
        self.pdb_factory = lambda: self.CustomPdb(self)
        self.pdb = None
        self.allow_breakpoints = True

    def add_handler_generator(self, handler: typing.Generator[None, Event, None]):
        handler.send(None)

        def handler_wrapper(event: Event):
            try:
                handler.send(event)
                return False
            except StopIteration:
                return True

        self.handlers.append(handler_wrapper)

    def add_handler(self, handler: typing.Callable[[Event], bool]):
        self.handlers.append(handler)

    def settrace(self, func):
        self.sub_tracer = func

        # Patch f_trace set by the debugger
        frame: types.FrameType = sys._getframe().f_back
        while frame and frame != self.base_frame:
            if frame.f_trace is not None:
                frame.f_trace = self.get_tracer(frame.f_trace)
            frame = frame.f_back

    def should_skip_frame_recursively(self, frame: types.FrameType) -> bool:
        """Determine if the frame and all its subframes should be skipped."""
        return (
            frame.f_code in self.own_function_codes
            or frame.f_code.co_filename in self.debugger_files
            or frame.f_code.co_filename.startswith(self.excluded_prefixes)
        )

    def get_tracer(self, sub_tracer):
        def tracer(frame: types.FrameType, event: str, arg: any) -> any:
            # Skip frames that should be skipped
            while self.skipped_frames:
                # Is the current frame called by the last skipped frame?
                if frame.f_back == self.skipped_frames[-1]:
                    self.skipped_frames.append(frame)
                    return None  # Returning None makes us skip the current function but not calls made from it
                # Otherwise we're done with the last skipped frame so pop it
                self.skipped_frames.pop()

            if self.should_skip_frame_recursively(frame):
                self.skipped_frames.append(frame)
                return None

            try:
                # Call the user-defined handlers, remove the ones that return True
                self.handlers = [
                    h
                    for h in self.handlers
                    if not h(Event(self.count, frame, event, arg))
                ]

                # Call the sub-tracer if it exists
                actual_sub_tracer = self.sub_tracer or sub_tracer
                self.sub_tracer = None
                if actual_sub_tracer:
                    # Disable patching while calling the sub-tracer to not interfere with the debugger
                    with patching.SetPatchingMode(patching.PatchingMode.OFF):
                        new_tracer = actual_sub_tracer(frame, event, arg)
                    if new_tracer != sub_tracer:
                        return self.get_tracer(new_tracer)
                return tracer
            except bdb.BdbQuit:
                # Exit the program
                exit(1)
            finally:
                self.count += 1

        return tracer

    def __enter__(self):
        self.backup()
        self.start()

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

        def do_count(self, arg: str):
            print(self.counter.count)
