import os
import sys
import random
import types
import pdb

class FrameCounter:
    excluded_prefixes = (sys.prefix, os.path.dirname(os.__file__), "<frozen", "<string>")
    debugger_files = (pdb.__file__,)

    def __init__(self, func, handler=None):
        self.func = func
        self.handler = handler or self.print_handler
        self.frames = []
        self.count = 0
        self.old_tracer = sys.gettrace()
        self.old_settrace = sys.settrace
        self.sub_tracer = None
        self.skipped_frame = None
        self.run()

    def settrace(self, func):
        # print("settrace called with", func)
        self.sub_tracer = func

    @classmethod
    def is_standard_library_frame(cls, frame: types.FrameType) -> bool:
        """Determine if the frame is from standard library or user code."""
        filename = frame.f_code.co_filename
        return filename.startswith(cls.excluded_prefixes)

    def should_skip_frame(self, frame: types.FrameType, event: str = None) -> bool:
        """"Determine if the frame should be skipped due to being part of the debugger."""
        if self.skipped_frame:
            if event == "return" and self.skipped_frame.f_back is frame.f_back:
                self.skipped_frame = None
            return True
        if frame.f_code.co_filename in self.debugger_files:
            self.skipped_frame = frame
            return True
        frame_self = frame.f_locals.get("self")
        return frame_self is self

    def my_tracer(self, frame: types.FrameType, event: str, arg: any) -> any:
        if not self.should_skip_frame(frame, event):
            if not self.is_standard_library_frame(frame):
                self.handler(self.count, frame, event, arg)
                self.frames.append(frame)
                self.count += 1
            if self.sub_tracer:
                self.sub_tracer = self.sub_tracer(frame, event, arg)
        return self.my_tracer

    def run(self):
        sys.settrace(self.my_tracer)
        sys.settrace = self.settrace
        try:
            self.func()
            assert sys.settrace == self.settrace
        finally:
            sys.settrace = self.old_settrace
            sys.settrace(self.old_tracer)

    @staticmethod
    def print_handler(count: int, frame: types.FrameType, event: str, arg: any):
        code = frame.f_code
        func_name = code.co_name
        file = code.co_filename
        line_no = frame.f_lineno
        print(f"#{count} {event} {func_name}() at line {line_no} of {file}")

def foo():
    print("foo")

def test():
    print("start")
    for i in range(10):
        if i == 8:
            print("breakpoint 1")
            breakpoint()
        print(i)
        foo()
        print("random", i)
        random.randint(0, 10)
    print("end")
    return 42

def handler(count, frame, event, arg):
    FrameCounter.print_handler(count, frame, event, arg)
    if count == 23:
        print("breakpoint 2")
        breakpoint()

counter = FrameCounter(test, handler)

print("Number of frames:", len(counter.frames))
