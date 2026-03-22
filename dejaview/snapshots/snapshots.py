import bisect
import multiprocessing as mp
import os
import signal
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, NoReturn, cast

from dejaview.counting.counting import CounterPosition, CounterPositionPattern
from dejaview.patching.patching import PatchingMode, set_patching_mode
from dejaview.snapshots.safe_fork import safe_fork

# Debug mode flag - set to False to disable debug logging
DEBUG = False

# Configuration defaults
DEFAULT_SNAPSHOT_INTERVAL = 1000  # instructions between snapshots
DEFAULT_MAX_SNAPSHOTS = 10  # maximum number of snapshots to keep


def debug_log(*args: Any) -> None:
    """Print debug messages to stderr if DEBUG is enabled."""
    if DEBUG:
        print(*args, file=sys.stderr, flush=True)


class ProcessType(Enum):
    ROOT = 0
    SNAPSHOT = 1
    REPLAY = 2


@dataclass
class SnapshotInfo:
    """Metadata about a snapshot."""

    position: CounterPosition


class _Snapshot[ArgType, ReturnType]:
    @set_patching_mode(PatchingMode.OFF)
    def __init__(
        self,
        snapshot_info: SnapshotInfo,
    ):
        self.info = snapshot_info
        self.arg_queue: mp.SimpleQueue = mp.SimpleQueue()  # replay arguments
        self.exit_code_queue: mp.SimpleQueue = mp.SimpleQueue()  # exit code
        self.return_queue: mp.SimpleQueue = mp.SimpleQueue()  # return value
        self.snapshot_pid: int | None = (
            None  # PID of the snapshot process, or None if terminated
        )

    def is_alive(self) -> bool:
        """Check whether the snapshot process is still running."""
        if self.snapshot_pid is None:
            return False
        try:
            with set_patching_mode(PatchingMode.OFF):
                os.kill(self.snapshot_pid, 0)  # signal 0 = existence check
            return True
        except OSError:
            return False

    @set_patching_mode(PatchingMode.OFF)
    def resume(self, arg: ArgType) -> ReturnType:
        self.arg_queue.put(arg)
        debug_log(
            f"DEBUG: resuming snapshot at position={self.info.position}"
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
                with set_patching_mode(PatchingMode.OFF):
                    os.kill(self.snapshot_pid, signal.SIGTERM)
                    os.waitpid(self.snapshot_pid, 0)
            except (OSError, ChildProcessError):
                # Process may have already exited if its replay child
                # crashed or if the OS reaped it.
                pass
            self.snapshot_pid = None


class SnapshotManager[ArgType, ReturnType]:
    """
    Manages multiple snapshots for efficient reverse debugging.

    Instead of always replaying from the beginning, we maintain multiple
    snapshots taken at strategic points during execution. When reversing,
    we resume from the nearest snapshot before the target position.
    """

    def __init__(
        self,
        snapshot_interval: int = DEFAULT_SNAPSHOT_INTERVAL,
        max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
    ):
        # is this process the root process or a snapshot child process?
        self.process_type: ProcessType = ProcessType.ROOT

        # available only in replay process
        self.return_queue: mp.SimpleQueue | None = None

        # available only in root process
        self.snapshots: list[_Snapshot[ArgType, ReturnType]] = []

        # Configuration for automatic snapshotting
        self.snapshot_interval = snapshot_interval
        self.max_snapshots = max_snapshots
        self._last_snapshot_count = 0

    @property
    def is_replay_process(self) -> bool:
        """
        Are we in a replay process? That means:
        - we should eventually exit by calling `return_from_replay`
        - we should not capture or resume snapshots
        - we should never run past the end of the recorded history
        """
        return self.process_type == ProcessType.REPLAY

    @set_patching_mode(PatchingMode.OFF)
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
        """Check if a new snapshot at instruction_count would be wasteful.

        If the gap from the last snapshot to the new one is smaller than
        all existing gaps, the new snapshot is in the densest region and
        would likely be the next eviction candidate.  Skip the fork to avoid
        wasting resources.
        """
        if len(self.snapshots) <= 1:
            return False

        new_gap = instruction_count - self.snapshots[-1].info.position.global_.count
        for i in range(1, len(self.snapshots)):
            existing_gap = (
                self.snapshots[i].info.position.global_.count
                - self.snapshots[i - 1].info.position.global_.count
            )
            if existing_gap <= new_gap:
                return False  # Existing gap is smaller, new snapshot adds value

        return True  # New gap is the smallest, snapshot would be wasteful

    @set_patching_mode(PatchingMode.OFF)
    def capture_snapshot(self, position: CounterPosition) -> ArgType | None:
        """
        Capture a snapshot by forking the root process.

        Args:
            position: The counter position for this snapshot

        Returns `None` in the root process, and the argument passed to
        `resume_snapshot` in the replay process.
        """
        assert self.process_type == ProcessType.ROOT, (
            "Only root process can capture snapshots"
        )

        # Evict a snapshot if at capacity, or skip if the new one would
        # be immediately redundant
        if len(self.snapshots) >= self.max_snapshots:
            if self._should_skip_capture(position.global_.count):
                self._last_snapshot_count = position.global_.count
                return None
            self._evict_snapshot(incoming_count=position.global_.count)

        snapshot_info = SnapshotInfo(position=position)
        snapshot = _Snapshot[ArgType, ReturnType](snapshot_info)
        snapshot_pid = safe_fork()

        if snapshot_pid != 0:  # root process
            snapshot.snapshot_pid = snapshot_pid
            self.snapshots.append(snapshot)
            self._last_snapshot_count = position.global_.count
            debug_log(
                f"DEBUG: captured snapshot at position={position.global_}, "
                f"total snapshots={len(self.snapshots)}"
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
                with set_patching_mode(PatchingMode.OFF):
                    _, status = os.waitpid(replay_pid, 0)
                snapshot.exit_code_queue.put(os.WEXITSTATUS(status))

    def find_best_snapshot(self, target_pattern: CounterPositionPattern) -> int:
        """
        Find the index of the best snapshot to resume from.

        Returns the index of the snapshot with the largest position
        that is still <= target_pattern. Uses binary search since snapshots
        are always sorted by position.
        """
        if not self.snapshots:
            return 0
        idx = (
            bisect.bisect_right(
                self.snapshots,
                target_pattern,
                key=lambda s: target_pattern.position_key(s.info.position),
            )
            - 1
        )
        if idx == -1:
            raise RuntimeError(
                f"No snapshot at or before target count {target_pattern}. "
                "The initial snapshot may have been killed."
            )
        return idx

    def _evict_snapshot(self, incoming_count: int) -> None:
        """Remove the snapshot whose removal creates the smallest gap.

        For each candidate (except index 0), the merged gap after removal is
        simply ``next_count - prev_count``.  Evict the candidate with the
        smallest merged gap since it provides the least spacing benefit.
        """
        if len(self.snapshots) <= 1:
            return

        best_idx = 1
        best_merged_gap = float("inf")

        for candidate in range(1, len(self.snapshots)):
            prev_count = self.snapshots[candidate - 1].info.position.global_.count
            if candidate == len(self.snapshots) - 1:
                next_count = incoming_count
            else:
                next_count = self.snapshots[candidate + 1].info.position.global_.count
            merged_gap = next_count - prev_count
            if merged_gap < best_merged_gap:
                best_merged_gap = merged_gap
                best_idx = candidate

        evicted = self.snapshots.pop(best_idx)
        debug_log(f"DEBUG: evicting snapshot at position={evicted.info.position}")
        evicted.terminate()

    def resume_snapshot(
        self, arg: ArgType, target_count: CounterPositionPattern
    ) -> ReturnType:
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

        # Try the best snapshot first; if its process is dead, remove it
        # and fall back to the next best until one works.
        while self.snapshots:
            snapshot_idx = self.find_best_snapshot(target_count)
            snapshot = self.snapshots[snapshot_idx]
            debug_log(
                f"DEBUG: resuming from snapshot {snapshot_idx} "
                f"at position={snapshot.info.position} for target={target_count}"
            )
            if not snapshot.is_alive():
                debug_log(
                    f"DEBUG: snapshot at position={snapshot.info.position} "
                    f"is dead, removing and trying next"
                )
                self.snapshots.pop(snapshot_idx)
                continue
            return snapshot.resume(arg)

        raise RuntimeError("All snapshot processes are dead")
