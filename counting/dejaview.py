from dataclasses import dataclass
from typing import Any
import typing

from .counting import FrameCounter, Event
from ..snapshots import SnapshotManager
from ..patching import patching
from ..patching.state_store import StateStore
from ..patching.setup import setup_patching


@dataclass
class State:
    to_count: int
    function_states: Any  # should be equivalent to get_type_hints(StateStore.serialize()).get('return')
    # TODO: add debugger state


class DejaView:
    def __init__(self):
        self.counter = FrameCounter()
        self.snapshot_manager = SnapshotManager()
        self.counter.pdb_factory = lambda: self.CustomPdb(self)

    def rerun_to(self, to_count: int):
        state = State(to_count, StateStore.serialize())
        raise self.snapshot_manager.resume_snapshot(state)

    def __enter__(self):
        self.counter.backup()
        self.setup_snapshot()
        self.counter.start()
        setup_patching()

    def setup_snapshot(self):
        # capture snapshot
        state: State = self.snapshot_manager.capture_snapshot()
        if state is not None:
            # add handler to enter debugger at to_count
            def handler():
                with patching.SetPatchingMode(patching.PatchingMode.MUTED):
                    while True:
                        event = yield
                        if event.count == state.to_count:
                            self.counter.allow_breakpoints = True
                            # print("enter breakpoint after stepping back to count", count)
                            self.counter.breakpoint(event.frame)
                            break

            self.counter.allow_breakpoints = False
            self.counter.add_handler_generator(handler())

            # set function state stores
            StateStore.deserialize(state.function_states)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.counter.__exit__(exc_type, exc_val, exc_tb)

    class CustomPdb(FrameCounter.CustomPdb):
        def __init__(self, dejaview: "DejaView"):
            super().__init__(dejaview.counter)
            self.dejaview = dejaview

        def do_back(self, arg: str):
            if not arg:
                arg = 1
            elif arg.isdigit():
                arg = int(arg)
            else:
                print("Invalid argument, must be a number")
                return

            count = self.counter.count - arg
            if not 0 <= count < self.counter.count:
                print(f"Invalid count, must be between 1 and {self.counter.count}")
                return
            self.quitting = True
            self.dejaview.rerun_to(count)


def print_handler(event: Event):
    code = event.frame.f_code
    func_name = code.co_name
    file = code.co_filename
    line_no = event.frame.f_lineno
    print(f"#{event.count} {event.event} {func_name}() at line {line_no} of {file}")
