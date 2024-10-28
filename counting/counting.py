import os
import sys
import random
import types
import pdb
import bdb
from dataclasses import dataclass
from typing import Any
from ..snapshots import SnapshotManager
from ..patching.state_store import StateStore
from ..patching import patching
from ..patching.setup import setup_patching


@dataclass
class State:
    to_count: int
    function_states: Any  # should be equivalent to get_type_hints(StateStore.serialize()).get('return')

class FrameCounter:
    excluded_prefixes = (
        sys.prefix,
        os.path.dirname(os.__file__),
        "<frozen",
        "<string>",
    )
    debugger_files = (pdb.__file__, bdb.__file__)

    class RerunException(Exception):
        pass

    def __init__(self):
        self.handlers = []
        self.count = 0
        self.sub_tracer = None
        self.own_function_codes = (
            self.settrace.__code__,
            self.breakpointhook.__code__,
        )
        self.skipped_frames = []
        self.pdb = None
        self.allow_breakpoints = True
        self.snapshot_manager = SnapshotManager()

    def add_handler(self, handler):
        self.handlers.append(handler)

    def settrace(self, func):
        self.sub_tracer = func

        # Patch f_trace set by the debugger
        frame: types.FrameType = sys._getframe().f_back
        while frame and frame != self.base_frame:
            if frame.f_trace is not None:
                frame.f_trace = self.get_tracer(frame.f_trace)
            frame = frame.f_back

    def should_skip_patch_in_frame_recursively(self, frame: types.FrameType) -> bool:
        return frame.f_code.co_filename in self.debugger_files

    def should_skip_frame_recursively(self, frame: types.FrameType) -> bool:
        """ "Determine if the frame and all its subframes should be skipped."""
        return (
            frame.f_code in self.own_function_codes
            or self.should_skip_patch_in_frame_recursively(frame)
            or frame.f_code.co_filename.startswith(self.excluded_prefixes)
        )

    def get_tracer(self, sub_tracer):
        def tracer(frame: types.FrameType, event: str, arg: any) -> any:
            # Skip frames that should be skipped
            while self.skipped_frames:
                if frame.f_back == self.skipped_frames[-1]:
                    if patching.is_skip_patching() or self.should_skip_patch_in_frame_recursively(frame):
                        print_handler(self.count, frame, event, arg)
                        patching.skip_patching()
                    self.skipped_frames.append(frame)
                    return None  # Returning None makes us skip the current function but not calls made from it
                if patching.is_skip_patching():
                    print_handler(self.count, frame, event, arg)
                    patching.restore_patching()
                self.skipped_frames.pop()

            if self.should_skip_frame_recursively(frame):
                if self.should_skip_patch_in_frame_recursively(frame):
                    print_handler(self.count, frame, event, arg)
                    patching.skip_patching()
                self.skipped_frames.append(frame)
                return None
            
            assert not patching.is_skip_patching()

            try:
                # Call the user-defined handlers, remove the ones that return True
                self.handlers = [
                    h for h in self.handlers if not h(self.count, frame, event, arg)
                ]

                # Call the sub-tracer if it exists
                actual_sub_tracer = self.sub_tracer or sub_tracer
                self.sub_tracer = None
                if actual_sub_tracer:
                    new_tracer = actual_sub_tracer(frame, event, arg)
                    if new_tracer != sub_tracer:
                        return self.get_tracer(new_tracer)
                return tracer
            finally:
                self.count += 1

        return tracer

    def rerun_to(self, to_count: int):
        state = State(to_count, StateStore.serialize())
        raise self.snapshot_manager.resume_snapshot(state)

    def __enter__(self):
        self.old_tracer = sys.gettrace()
        self.old_settrace = sys.settrace
        self.old_breakpointhook = sys.breakpointhook
        sys.settrace = self.settrace
        self.setup()

        # capture snapshot
        state: State = self.snapshot_manager.capture_snapshot()
        if state != None:
            # add handler to enter debugger at to_count
            def handler(count, frame, event, arg):
                if count == state.to_count:
                    self.allow_breakpoints = True
                    print("enter breakpoint after stepping back to count", count)
                    self.get_pdb().set_trace(frame)
                    return True
                return False
            self.allow_breakpoints = False
            self.add_handler(handler)

            # set function state stores
            StateStore.deserialize(state.function_states)

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore the original tracer and settrace
        self.old_settrace(self.old_tracer)
        sys.settrace = self.old_settrace
        sys.breakpointhook = self.old_breakpointhook

    def setup(self):
        setup_patching()
        self.count = 0
        self.sub_tracer = None
        self.base_frame = sys._getframe().f_back
        sys.breakpointhook = self.breakpointhook
        self.old_settrace(self.get_tracer(None))
        return self

    def get_pdb(self):
        if self.pdb is None:
            self.pdb = self.CustomPdb(self)
        return self.pdb

    def breakpointhook(self, *args, **kws):
        if self.allow_breakpoints:
            self.get_pdb().set_trace(sys._getframe().f_back)

    class CustomPdb(pdb.Pdb):
        def __init__(self, counter):
            super().__init__()
            self.counter = counter

        def do_back(self, arg):
            if not arg:
                arg = 1
            elif not arg.isdigit():
                print("Invalid argument, must be a number")
                return
            count = self.counter.count - int(arg)
            if not 0 <= count < self.counter.count:
                print(f"Invalid count, must be between 1 and {self.counter.count}")
                return
            self.quitting = True
            self.counter.rerun_to(count)

        def do_count(self, arg):
            print(self.counter.count)


def foo():
    print("foo")


def test():
    print("start")
    for i in range(10):
        if i == 8:
            print("breakpoint 1")
            breakpoint()
            print("after breakpoint 1")  # should stop here
        print(i)
        foo()
        print("random", i)
        random.randint(0, 10)
    print("end1")
    print("end2")
    print("end3")
    return 42


def print_handler(count: int, frame: types.FrameType, event: str, arg: any):
    code = frame.f_code
    func_name = code.co_name
    file = code.co_filename
    line_no = frame.f_lineno
    print(f"#{count} {event} {func_name}() at line {line_no} of {file}")


def test_input():
    import time

    print("start")
    # s = input("input something: ")
    s = time.time()
    print("input:", s)
    breakpoint()
    print("input:", s)
    print("finish")


counter = FrameCounter()
# counter.add_handler(print_handler)
with counter:
    test_input()

print("Number of frames:", counter.count)
