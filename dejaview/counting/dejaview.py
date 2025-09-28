from dataclasses import dataclass
from typing import Any, List

from .counting import FrameCounter, Event
from ..snapshots import SnapshotManager
from ..patching import patching
from ..patching.state_store import StateStore
from ..patching.setup import setup_patching


@dataclass
class State:
    to_counts: List[int]
    function_states: Any  # should be equivalent to get_type_hints(StateStore.serialize()).get('return')
    # TODO: add debugger state


class DejaView:
    def __init__(self):
        self.counter = FrameCounter()
        self.snapshot_manager = SnapshotManager()
        self.counter.pdb_factory = lambda: self.CustomPdb(self)
        # self.counter.add_handler(print_handler)

    def step_back(self):
        counts = [frame.count for frame in self.counter.stack]
        if counts[-1] == 0:
            counts.pop()
        else:
            counts[-1] -= 1
        state = State(counts, StateStore.serialize())
        self.snapshot_manager.resume_snapshot(state)

    def __enter__(self):
        self.counter.backup()
        self.setup_snapshot()
        self.counter.start()
        self.patches = setup_patching()
        return self

    def setup_snapshot(self):
        # capture snapshot
        state: State = self.snapshot_manager.capture_snapshot()
        if state is not None:  # if we're resuming from a snapshot
            # add handler to enter debugger at to_count
            def handler():
                with patching.SetPatchingMode(patching.PatchingMode.MUTED):
                    while True:
                        event = yield
                        # TODO optimize
                        counts = [frame.count for frame in event.stack]
                        if state.to_counts == counts:
                            self.counter.allow_breakpoints = True
                            # print("enter breakpoint after stepping back to count", state.to_count)
                            self.counter.breakpoint(event.frame)
                            break

            self.counter.allow_breakpoints = False
            self.counter.add_handler_generator(handler())

            # set function state stores
            StateStore.deserialize(state.function_states)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.patches.__exit__(exc_type, exc_val, exc_tb)
        self.counter.__exit__(exc_type, exc_val, exc_tb)

    def get_pdb(self):
        return self.counter.get_pdb()

    class CustomPdb(FrameCounter.CustomPdb):
        def __init__(self, dejaview: "DejaView"):
            super().__init__(dejaview.counter)
            self.dejaview = dejaview

        def do_back(self, arg: str):
            self.quitting = True
            self.dejaview.step_back()

        def stop_here(self, frame):
            return self.counter.allow_breakpoints and super().stop_here(frame)


def print_handler(event: Event):
    code = event.frame.f_code
    func_name = code.co_name
    file = code.co_filename
    line_no = event.frame.f_lineno
    print(f"#{event.count} {event.event} {func_name}() at line {line_no} of {file}")
