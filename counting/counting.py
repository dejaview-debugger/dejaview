import os
import sys
import random
import types
import pdb
from dataclasses import dataclass


class FrameCounter:
    excluded_prefixes = (
        sys.prefix,
        os.path.dirname(os.__file__),
        "<frozen",
        "<string>",
    )
    debugger_files = (pdb.__file__,)

    class RerunException(Exception):
        pass

    def __init__(self, func):
        self.func = func
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

    def should_skip_frame_recursively(self, frame: types.FrameType) -> bool:
        """ "Determine if the frame and all its subframes should be skipped."""
        return (
            frame.f_code in self.own_function_codes
            or frame.f_code.co_filename in self.debugger_files
            or frame.f_code.co_filename.startswith(self.excluded_prefixes)
        )

    def get_tracer(self, sub_tracer):
        def tracer(frame: types.FrameType, event: str, arg: any) -> any:
            # Skip frames that should be skipped
            while self.skipped_frames:
                if frame.f_back == self.skipped_frames[-1]:
                    self.skipped_frames.append(frame)
                    return None  # Returning None makes us skip the current function but not calls made from it
                self.skipped_frames.pop()

            if self.should_skip_frame_recursively(frame):
                self.skipped_frames.append(frame)
                return None

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
        def handler(count, frame, event, arg):
            if count == to_count:
                self.allow_breakpoints = True
                self.get_pdb().set_trace(frame)
                return True
            return False

        self.add_handler(handler)
        self.allow_breakpoints = False
        raise self.RerunException()

    def run(self):
        # Backup system tracer and settrace
        old_tracer = sys.gettrace()
        self.old_settrace = sys.settrace
        old_breakpointhook = sys.breakpointhook
        sys.settrace = self.settrace
        try:
            while True:
                try:
                    self.setup()
                    self.func()
                    break
                except self.RerunException:
                    pass
        finally:
            # Restore the original tracer and settrace
            self.old_settrace(old_tracer)
            sys.settrace = self.old_settrace
            sys.breakpointhook = old_breakpointhook

    def setup(self):
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


def handler(count, frame, event, arg):
    FrameCounter.print_handler(count, frame, event, arg)
    if count == 23:
        print("breakpoint 2")
        breakpoint()
        print("after breakpoint 2")  # should not stop here


counter = FrameCounter(test)
counter.add_handler(print_handler)
counter.run()
# test()

print("Number of frames:", counter.count)
