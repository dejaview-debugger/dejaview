import multiprocessing as mp
import os
import random
import signal
import sys
import typing
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
        self.snapshot_pid: int | None = None  # PID of the snapshot process

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
                pass  # Process already terminated
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

        # Callback for when checkpoints are captured
        self._on_checkpoint_captured: typing.Callable[[CheckpointInfo], None] | None = (
            None
        )

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

    def capture_snapshot(
        self, instruction_count: int = 0, is_automatic: bool = False
    ) -> ArgType | None:
        """
        Capture a snapshot by forking the root process.

        Args:
            instruction_count: The current instruction count for this checkpoint
            is_automatic: If True, this is an automatic checkpoint (subject to eviction)

        Returns `None` in the root process, and the argument passed to
        `resume_snapshot` in the replay process.
        """
        assert self.process_type == ProcessType.ROOT, (
            "Only root process can capture snapshots"
        )

        # Evict oldest non-initial checkpoint if at limit (for automatic checkpoints)
        if is_automatic and len(self.snapshots) >= self.max_checkpoints:
            self._evict_oldest_checkpoint()

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
            if self._on_checkpoint_captured:
                self._on_checkpoint_captured(checkpoint_info)
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

    def maybe_capture_checkpoint(self, current_count: int) -> bool:
        """
        Capture a checkpoint if enough instructions have elapsed since the last one.

        Args:
            current_count: The current instruction count

        Returns:
            True if a checkpoint was captured, False otherwise
        """
        if self.process_type != ProcessType.ROOT:
            return False  # Only root process can capture checkpoints

        if current_count - self._last_checkpoint_count < self.checkpoint_interval:
            return False

        self.capture_snapshot(instruction_count=current_count, is_automatic=True)
        return True

    def find_best_checkpoint(self, target_count: int) -> int:
        """
        Find the index of the best checkpoint to resume from.

        Returns the index of the checkpoint with the largest instruction_count
        that is still <= target_count.
        """
        best_idx = 0
        for i, snapshot in enumerate(self.snapshots):
            if snapshot.info.instruction_count <= target_count:
                best_idx = i
            else:
                break  # snapshots are sorted by instruction count
        return best_idx

    def _evict_oldest_checkpoint(self) -> None:
        """Remove the oldest checkpoint (except the initial one at count=0)."""
        if len(self.snapshots) <= 1:
            return

        # Remove the second oldest (keep the initial snapshot)
        old_snapshot = self.snapshots.pop(1)
        debug_log(
            f"DEBUG: evicting checkpoint at count={old_snapshot.info.instruction_count}"
        )
        old_snapshot.terminate()

    def resume_snapshot(
        self, arg: ArgType, target_count: int | None = None
    ) -> ReturnType:
        """
        Resume the best snapshot for the given target count.

        Args:
            arg: The argument to pass to the replay process
            target_count: The target instruction count. If None, uses first snapshot.

        Returns:
            The return value from the replay process
        """
        assert self.process_type == ProcessType.ROOT, (
            "Only root process can resume snapshots"
        )

        if len(self.snapshots) == 0:
            raise RuntimeError("No snapshots to resume")

        # Find the best checkpoint to resume from
        if target_count is not None:
            checkpoint_idx = self.find_best_checkpoint(target_count)
        else:
            checkpoint_idx = 0

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
