import bisect
import multiprocessing as mp
import os
import random
import signal
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, NoReturn, cast

from dejaview.snapshots.safe_fork import safe_fork

# Debug mode flag - set to False to disable debug logging
DEBUG = False

# Configuration defaults
DEFAULT_CHECKPOINT_INTERVAL = 1000  # instructions between checkpoints
DEFAULT_MAX_CHECKPOINTS = 10  # maximum number of snapshots to keep


def debug_log(*args: Any) -> None:
    """Print debug messages to stderr if DEBUG is enabled."""
    if DEBUG:
        print(*args, file=sys.stderr, flush=True)


class ProcessType(Enum):
    ROOT = 0
    SNAPSHOT = 1
    REPLAY = 2


@dataclass
class CheckpointInfo:
    """Metadata about a checkpoint."""

    instruction_count: int  # Global instruction count when snapshot was taken
    # Note: We don't store CounterPosition here because it's passed at resume time


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
            state = manager.capture_snapshot(instruction_count=i)
            if state is not None:
                print("got state:", state)

    input("input: ")
    print(pid, "random state:", hash(random.getstate()))
    print(pid, "random number:", random.randint(0, 100))
    print(pid, res)
    manager.resume_snapshot("message from fork", target_count=5)


class _Snapshot[ArgType, ReturnType]:
    def __init__(
        self,
        checkpoint_info: CheckpointInfo,
    ):
        self.info = checkpoint_info
        self.arg_queue: mp.SimpleQueue = mp.SimpleQueue()  # replay arguments
        self.exit_code_queue: mp.SimpleQueue = mp.SimpleQueue()  # exit code
        self.return_queue: mp.SimpleQueue = mp.SimpleQueue()  # return value
        self.snapshot_pid: int | None = (
            None  # PID of the snapshot process, or None if terminated
        )

    def resume(self, arg: ArgType) -> ReturnType:
        self.arg_queue.put(arg)
        debug_log(
            f"DEBUG: resuming snapshot at count={self.info.instruction_count} "
            f"in replay process"
        )
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

    def terminate(self) -> None:
        """Terminate the snapshot process to free resources."""
        if self.snapshot_pid is not None:
            try:
                os.kill(self.snapshot_pid, signal.SIGTERM)
                os.waitpid(self.snapshot_pid, 0)
            except (OSError, ChildProcessError):
                # Process may have already exited if its replay child
                # crashed or if the OS reaped it.
                pass
            self.snapshot_pid = None


class SnapshotManager[ArgType, ReturnType]:
    """
    Manages multiple snapshots (checkpoints) for efficient reverse debugging.

    Instead of always replaying from the beginning, we maintain multiple
    snapshots taken at strategic points during execution. When reversing,
    we resume from the nearest checkpoint before the target position.
    """

    def __init__(
        self,
        checkpoint_interval: int = DEFAULT_CHECKPOINT_INTERVAL,
        max_checkpoints: int = DEFAULT_MAX_CHECKPOINTS,
    ):
        # is this process the root process or a snapshot child process?
        self.process_type: ProcessType = ProcessType.ROOT

        # available only in replay process
        self.return_queue: mp.SimpleQueue | None = None

        # available only in root process
        self.snapshots: list[_Snapshot[ArgType, ReturnType]] = []

        # Configuration for automatic checkpointing
        self.checkpoint_interval = checkpoint_interval
        self.max_checkpoints = max_checkpoints
        self._last_checkpoint_count = 0

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

    def _should_skip_capture(self, instruction_count: int) -> bool:
        """Check if a new checkpoint at instruction_count would be wasteful.

        If the gap from the last checkpoint to the new one is smaller than
        all existing gaps, the new checkpoint is in the densest region and
        would likely be the next eviction candidate.  Skip the fork to avoid
        wasting resources.
        """
        if len(self.snapshots) <= 1:
            return False

        new_gap = instruction_count - self.snapshots[-1].info.instruction_count
        for i in range(1, len(self.snapshots)):
            existing_gap = (
                self.snapshots[i].info.instruction_count
                - self.snapshots[i - 1].info.instruction_count
            )
            if existing_gap <= new_gap:
                return False  # Existing gap is smaller, new checkpoint adds value

        return True  # New gap is the smallest, checkpoint would be wasteful

    def capture_snapshot(self, instruction_count: int = 0) -> ArgType | None:
        """
        Capture a snapshot by forking the root process.

        Args:
            instruction_count: The current instruction count for this checkpoint

        Returns `None` in the root process, and the argument passed to
        `resume_snapshot` in the replay process.
        """
        assert self.process_type == ProcessType.ROOT, (
            "Only root process can capture snapshots"
        )

        # Evict a checkpoint if at capacity, or skip if the new one would
        # be immediately redundant
        if len(self.snapshots) >= self.max_checkpoints:
            if self._should_skip_capture(instruction_count):
                self._last_checkpoint_count = instruction_count
                return None
            self._evict_checkpoint(incoming_count=instruction_count)

        checkpoint_info = CheckpointInfo(instruction_count=instruction_count)
        snapshot = _Snapshot[ArgType, ReturnType](checkpoint_info)
        snapshot_pid = safe_fork()

        if snapshot_pid != 0:  # root process
            snapshot.snapshot_pid = snapshot_pid
            self.snapshots.append(snapshot)
            self._last_checkpoint_count = instruction_count
            debug_log(
                f"DEBUG: captured checkpoint at count={instruction_count}, "
                f"total checkpoints={len(self.snapshots)}"
            )
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

    def find_best_checkpoint(self, target_count: int) -> int:
        """
        Find the index of the best checkpoint to resume from.

        Returns the index of the checkpoint with the largest instruction_count
        that is still <= target_count. Uses binary search since snapshots
        are always sorted by instruction count.
        """
        if not self.snapshots:
            return 0
        idx = (
            bisect.bisect_right(
                self.snapshots,
                target_count,
                key=lambda s: s.info.instruction_count,
            )
            - 1
        )
        return max(idx, 0)

    def _evict_checkpoint(self, incoming_count: int) -> None:
        """Remove the checkpoint whose removal creates the smallest gap.

        For each candidate (except index 0), the merged gap after removal is
        simply ``next_count - prev_count``.  Evict the candidate with the
        smallest merged gap since it provides the least spacing benefit.
        """
        if len(self.snapshots) <= 1:
            return

        best_idx = 1
        best_merged_gap = float("inf")

        for candidate in range(1, len(self.snapshots)):
            prev_count = self.snapshots[candidate - 1].info.instruction_count
            if candidate == len(self.snapshots) - 1:
                next_count = incoming_count
            else:
                next_count = self.snapshots[candidate + 1].info.instruction_count
            merged_gap = next_count - prev_count
            if merged_gap < best_merged_gap:
                best_merged_gap = merged_gap
                best_idx = candidate

        evicted = self.snapshots.pop(best_idx)
        debug_log(
            f"DEBUG: evicting checkpoint at count={evicted.info.instruction_count}"
        )
        evicted.terminate()

    def resume_snapshot(self, arg: ArgType, target_count: int) -> ReturnType:
        """
        Resume the best snapshot for the given target count.

        Args:
            arg: The argument to pass to the replay process
            target_count: The target instruction count to resume near.

        Returns:
            The return value from the replay process
        """
        assert self.process_type == ProcessType.ROOT, (
            "Only root process can resume snapshots"
        )

        if len(self.snapshots) == 0:
            raise RuntimeError("No snapshots to resume")

        checkpoint_idx = self.find_best_checkpoint(target_count)
        snapshot = self.snapshots[checkpoint_idx]
        debug_log(
            f"DEBUG: resuming from checkpoint {checkpoint_idx} "
            f"at count={snapshot.info.instruction_count} for target={target_count}"
        )
        return snapshot.resume(arg)

    def get_checkpoint_count(self) -> int:
        """Return the number of active checkpoints."""
        return len(self.snapshots)

    def get_checkpoint_info(self) -> list[CheckpointInfo]:
        """Return info about all active checkpoints."""
        return [s.info for s in self.snapshots]


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
