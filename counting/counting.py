import os
import sys
import random
import types
import pdb

class FrameCounter:
    excluded_prefixes = (sys.prefix, os.path.dirname(os.__file__), "<frozen", "<string>")
    debugger_files = (pdb.__file__,)

    def __init__(self, handler=None):
        self.handler = handler or self.print_handler
        # self.frames = []
        self.count = 0
        self.old_tracer = sys.gettrace()
        self.old_settrace = sys.settrace
        self.sub_tracer = []

    def settrace(self, func):
        # print("settrace called with", func)
        self.sub_tracer = func

    @classmethod
    def is_standard_library_frame(cls, frame: types.FrameType) -> bool:
        """Determine if the frame is from standard library or user code."""
        filename = frame.f_code.co_filename
        return filename.startswith(cls.excluded_prefixes)

    def should_skip_frame(self, frame: types.FrameType) -> bool:
        """"Determine if the frame should be skipped due to being part of the debugger."""
        return frame.f_code.co_filename in self.debugger_files or frame.f_code == self.__exit__.__code__

    def get_tracer(self, sub_tracer, use_global):
        def tracer(frame: types.FrameType, event: str, arg: any) -> any:
            if self.should_skip_frame(frame):
                return None
            if not self.is_standard_library_frame(frame):
                self.handler(self.count, frame, event, arg)
                # self.frames.append(frame)
                self.count += 1
            actual_tracer = sub_tracer or (use_global and self.sub_tracer)
            if actual_tracer:
                return self.get_tracer(actual_tracer(frame, event, arg), use_global=False)
            return tracer
        return tracer

    def __enter__(self):
        sys.settrace(self.get_tracer(None, use_global=True))
        sys.settrace = self.settrace
        return self

    def __exit__(self, exc_type, exc_value, traceback):
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
    # random.randint(0, 4)

def test():
    print("start")
    for i in range(10):
        if i == 8:
            print("breakpoint 1")
            # breakpoint()
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

# def trace(frame, event, arg):
#     return trace
# sys.settrace(trace)
# test()
# sys.settrace(None)

with FrameCounter(handler) as counter:
    test()

# test()

print("Number of frames:", counter.count)
