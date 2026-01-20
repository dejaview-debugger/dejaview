import multiprocessing as mp
import os
import random
import sys
from enum import Enum
from typing import Any, NoReturn, cast

from dejaview.snapshots.safe_fork import safe_fork

# Debug mode flag - set to False to disable debug logging
DEBUG = False


def debug_log(*args: Any) -> None:
    """Print debug messages to stderr if DEBUG is enabled."""
    if DEBUG:
        print(*args, file=sys.stderr, flush=True)


class ProcessType(Enum):
    ROOT = 0
    SNAPSHOT = 1
    REPLAY = 2


# Test program for debug
def test():
    manager = SnapshotManager[str, Any]()
    res = [0, 1]

    for i in range(10):
        f_1 = res[-1]
        f_2 = res[-2]
        f_next = f_1 + f_2
        res.append(f_next)
        pid = os.getpid()
        print(pid, f_next)
        if i == 5:
            state = manager.capture_snapshot()
            if state is not None:
                print("got state:", state)

    input("input: ")
    print(pid, "random state:", hash(random.getstate()))
    print(pid, "random number:", random.randint(0, 100))
    print(pid, res)
    manager.resume_snapshot("message from fork")


class _Snapshot[ArgType, ReturnType]:
    def __init__(
        self,
    ):
        self.arg_queue: mp.SimpleQueue = mp.SimpleQueue()  # replay arguments
        self.exit_code_queue: mp.SimpleQueue = mp.SimpleQueue()  # exit code
        self.return_queue: mp.SimpleQueue = mp.SimpleQueue()  # return value

    def resume(self, arg: ArgType) -> ReturnType:
        self.arg_queue.put(arg)
        debug_log("DEBUG: resuming snapshot in replay process")
        status = self.exit_code_queue.get()
        debug_log("DEBUG: replay process exited with status", status)
        if status != 0:
            # propagate error from replay process if it crashed
            sys.exit(status)
        else:
            # get return value from replay process
            if self.return_queue.empty():
                raise RuntimeError("No return value from replay process")
            return cast(ReturnType, self.return_queue.get())


class SnapshotManager[ArgType, ReturnType]:
    """
    Singleton class to manage snapshots and replay processes.
    """

    def __init__(self):
        # is this process the root process or a snapshot child process?
        self.process_type: ProcessType = ProcessType.ROOT

        # available only in replay process
        self.return_queue: mp.SimpleQueue | None = None

        # available only in root process
        self.snapshots: list[_Snapshot[ArgType, ReturnType]] = []

    @property
    def is_replay_process(self) -> bool:
        """
        Are we in a replay process? That means:
        - we should eventually exit by calling `return_from_replay`
        - we should not capture or resume snapshots
        - we should never run past the end of the recorded history
        """
        return self.process_type == ProcessType.REPLAY

    def return_from_replay(self, ret: ReturnType) -> NoReturn:
        """
        Terminate the replay process and pass `ret` to the root process.
        """
        assert self.process_type == ProcessType.REPLAY, (
            "Only replay process can return from replay"
        )
        assert self.return_queue is not None
        debug_log(f"DEBUG: returning from replay process, {ret=}")
        self.return_queue.put(ret)
        # Quit directly bypassing any cleanup
        # This is important because cleanup code is "new" code which isn't allowed
        # to run in a replay process.
        os._exit(0)

    def capture_snapshot(self) -> ArgType | None:
        """
        Capture a snapshot by forking the root process.

        Returns `None` in the root process, and the argument passed to
        `resume_snapshot` in the replay process.
        """
        assert self.process_type == ProcessType.ROOT, (
            "Only root process can capture snapshots"
        )
        # print("capturing snapshot")
        snapshot = _Snapshot[ArgType, ReturnType]()
        snapshot_pid = safe_fork()
        if snapshot_pid != 0:  # root process
            # stores the id of the fork
            self.snapshots.append(snapshot)
            return None

        # snapshot process
        self.process_type = ProcessType.SNAPSHOT

        # The snapshot process permanently remains in this loop,
        # forking replay processes as needed.
        while True:
            arg = cast(ArgType, snapshot.arg_queue.get())
            replay_pid = safe_fork()
            if replay_pid == 0:  # replay process
                self.process_type = ProcessType.REPLAY
                self.return_queue = snapshot.return_queue
                return arg
            else:  # snapshot process
                _, status = os.waitpid(replay_pid, 0)
                snapshot.exit_code_queue.put(os.WEXITSTATUS(status))

    def resume_snapshot(self, arg: ArgType) -> ReturnType:
        """
        Resume the snapshot with argument `arg`.
        """
        assert self.process_type == ProcessType.ROOT, (
            "Only root process can resume snapshots"
        )

        if len(self.snapshots) == 0:
            raise RuntimeError("No snapshots to resume")

        assert len(self.snapshots) == 1, "TODO: support multiple snapshots"
        snapshot = self.snapshots[0]
        return snapshot.resume(arg)


"""
snapshots contains:
- instruction count
- variable state
- standard library functions

global snapshots contains:
- highest instruction count
"""

if __name__ == "__main__":
    test()
