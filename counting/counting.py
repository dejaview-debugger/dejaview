import os
import sys
import random
import types
import pdb
from dataclasses import dataclass

class FrameCounter:
    excluded_prefixes = (sys.prefix, os.path.dirname(os.__file__), "<frozen", "<string>")
    debugger_files = (pdb.__file__,)
    instance = []

    class RerunException(Exception):
        pass

    def __init__(self, func, handler=None):
        self.func = func
        self.handler = handler or self.print_handler
        self.breakpoint_count = -1
        self.breakpoint_handler = None
        self.count = 0
        self.sub_tracer = None
        self.own_function_codes = (self.settrace.__code__, self.get_tracer.__code__, CustomPdb.__init__.__code__)

    def settrace(self, func):
        self.sub_tracer = func

        # Patch f_trace set by the debugger
        frame: types.FrameType = sys._getframe().f_back
        while frame and frame != self.base_frame:
            if frame.f_trace is not None:
                frame.f_trace = self.get_tracer(frame.f_trace)
            frame = frame.f_back

    def should_skip_frame_recursively(self, frame: types.FrameType) -> bool:
        """"Determine if the frame and all its subframes should be skipped."""
        return frame.f_code in self.own_function_codes \
            or frame.f_code.co_filename in self.debugger_files \
            or frame.f_code.co_filename.startswith(self.excluded_prefixes)

    def get_tracer(self, sub_tracer):
        def tracer(frame: types.FrameType, event: str, arg: any) -> any:
            # Skip frames that are part of the standard library or the debugger
            if self.should_skip_frame_recursively(frame):
                return None

            try:
                # Call the user-defined handler
                self.handler(self.count, frame, event, arg)
                if self.count == self.breakpoint_count:
                    self.breakpoint_handler()

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

    def rerun_to(self, count: int, breakpoint_handler):
        self.breakpoint_count = count
        self.breakpoint_handler = breakpoint_handler
        raise self.RerunException()

    def run(self):
        # Backup system tracer and settrace
        self.old_tracer = sys.gettrace()
        self.old_settrace = sys.settrace
        sys.settrace = self.settrace
        FrameCounter.instance.append(self)
        try:
            while True:
                try:
                    self.setup()
                    self.func()
                    break
                except self.RerunException as e:
                    pass
        finally:
            # Restore the original tracer and settrace
            self.old_settrace(self.old_tracer)
            sys.settrace = self.old_settrace
            FrameCounter.instance.remove(self)

    def setup(self):
        self.count = 0
        self.base_frame = sys._getframe().f_back
        self.old_settrace(self.get_tracer(None))
        return self

    @staticmethod
    def print_handler(count: int, frame: types.FrameType, event: str, arg: any):
        code = frame.f_code
        func_name = code.co_name
        file = code.co_filename
        line_no = frame.f_lineno
        print(f"#{count} {event} {func_name}() at line {line_no} of {file}")

class CustomPdb(pdb.Pdb):
    def __init__(self):
        super().__init__()
        if FrameCounter.instance:
            self.counter = FrameCounter.instance[-1]
        else:
            self.counter = FrameCounter(None)

    def do_back(self, arg):
        if not arg:
            arg = 1
        count = self.counter.count - int(arg)
        if not 0 <= count < self.counter.count:
            print("Invalid count")
            return
        self.counter.rerun_to(count, self.set_trace)

    def do_count(self, arg):
        print(self.counter.count)

def foo():
    print("foo")

def test():
    print("start")
    for i in range(10):
        if i == 8:
            print("breakpoint 1")
            CustomPdb().set_trace()
            print("after breakpoint 1") # should stop here
        print(i)
        foo()
        print("random", i)
        random.randint(0, 10)
    print("end1")
    print("end2")
    print("end3")
    return 42

def handler(count, frame, event, arg):
    FrameCounter.print_handler(count, frame, event, arg)
    # if count == 23:
    #     print("breakpoint 2")
    #     breakpoint()
    #     print("after breakpoint 2") # should not stop here

counter = FrameCounter(test, handler)
counter.run()
# test()

print("Number of frames:", counter.count)
