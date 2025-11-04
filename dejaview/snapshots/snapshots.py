import atexit
import multiprocessing as mp
import os
import random
import signal
from typing import Any


# Test program for debug
def test():
    manager = SnapshotManager()
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


class Snapshot:
    def __init__(self, queue, pid):
        assert pid != 0
        self.queue = queue
        self.pid = pid

    def resume(self, state):
        # TODO: we also need to pass recorded information from patched functions to the
        # snapshot process
        self.queue.put(state)
        # TODO: what to do with the current process?
        _, status = os.waitpid(self.pid, 0)
        exit(status >> 8)


class SnapshotManager:
    def __init__(self):
        self.snapshots = []
        self.children = []

        # register `cleanup` routine to terminate unkilled child processes
        atexit.register(self.cleanup)

    def cleanup(self):
        # cleans up child processes on exit
        unkilled_children = []
        for pid in self.children:
            try:
                os.kill(pid, signal.SIGTERM)
                os.waitpid(pid, 0)
            except OSError:
                unkilled_children.append(pid)
        print(f"Possible unkilled child process: {unkilled_children}")

    def capture_snapshot(self):
        # print("capturing snapshot")
        queue: mp.SimpleQueue[tuple[Any | None, BaseException | None]] = (
            mp.SimpleQueue()
        )
        random_state = random.getstate()
        pid = os.fork()
        random.setstate(random_state)
        if pid == 0:  # child process
            # suspend the child process
            # print("snapshot paused")

            # SIGTERM handler
            def handle_sigterm(signum, frame):
                print(f"[Child {os.getpid()}] Received SIGTERM, exiting")
                os._exit(0)

            signal.signal(signal.SIGTERM, handle_sigterm)

            state = queue.get()
            queue.close()
            # print("snapshot resumed with:", state)
            # create another fork to use when we resume again
            new_state = self.capture_snapshot()
            return new_state or state
        else:  # parent process
            # stores the id of the fork
            self.snapshots.append(Snapshot(queue, pid))

            # keep track of pid so `SnapshotManager.cleanup` can terminate it later
            self.children.append(pid)
            return None

    def resume_snapshot(self, state):
        # TODO: decide if we want to keep a copy of original snapshot
        # after they have been resumed
        if len(self.snapshots) > 0:
            snapshot = self.snapshots.pop()

            # remove child's pid that will run its own `cleanup` routine from the parent
            # to avoid cleaning it up twice
            self.children = [c for c in self.children if c != snapshot.pid]
            snapshot.resume(state)
        else:
            # print("no snapshot to resume")
            pass


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
