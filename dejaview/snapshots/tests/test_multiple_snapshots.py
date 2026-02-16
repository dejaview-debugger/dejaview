"""Tests for multiple snapshots functionality."""

from unittest.mock import MagicMock, patch

from dejaview.counting.dejaview import DejaView
from dejaview.snapshots.snapshots import (
    CheckpointInfo,
    ProcessType,
    SnapshotManager,
)


def _make_mock_snapshot(instruction_count: int) -> MagicMock:
    """Create a mock _Snapshot with the given instruction count."""
    mock = MagicMock()
    mock.info = CheckpointInfo(instruction_count=instruction_count)
    return mock


class TestFindBestCheckpoint:
    """Tests for find_best_checkpoint using bisect."""

    def test_empty_snapshots_returns_zero(self):
        """With no snapshots, should return 0."""
        manager: SnapshotManager = SnapshotManager()
        assert manager.find_best_checkpoint(100) == 0

    def test_exact_match(self):
        """Should return the index of exact match."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [
            _make_mock_snapshot(0),
            _make_mock_snapshot(100),
            _make_mock_snapshot(200),
        ]  # type: ignore[assignment]
        assert manager.find_best_checkpoint(100) == 1

    def test_between_checkpoints_picks_earlier(self):
        """Should return the index of the checkpoint before target."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [
            _make_mock_snapshot(0),
            _make_mock_snapshot(100),
            _make_mock_snapshot(200),
        ]  # type: ignore[assignment]
        assert manager.find_best_checkpoint(150) == 1  # 100 is before 150

    def test_target_before_all_checkpoints(self):
        """Should return 0 when target is before all checkpoints."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [
            _make_mock_snapshot(100),
            _make_mock_snapshot(200),
        ]  # type: ignore[assignment]
        assert manager.find_best_checkpoint(50) == 0

    def test_target_after_all_checkpoints(self):
        """Should return the last index when target is after all checkpoints."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [
            _make_mock_snapshot(0),
            _make_mock_snapshot(100),
            _make_mock_snapshot(200),
        ]  # type: ignore[assignment]
        assert manager.find_best_checkpoint(300) == 2

    def test_single_checkpoint(self):
        """With single checkpoint, should return 0 for any target."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [_make_mock_snapshot(100)]  # type: ignore[assignment]
        assert manager.find_best_checkpoint(50) == 0
        assert manager.find_best_checkpoint(100) == 0
        assert manager.find_best_checkpoint(150) == 0


class TestEvictCheckpoint:
    """Tests for min-max-gap eviction policy."""

    def test_evict_noop_with_single_snapshot(self):
        """Eviction should do nothing with only the initial snapshot."""
        manager: SnapshotManager = SnapshotManager()
        s0 = _make_mock_snapshot(0)
        manager.snapshots = [s0]  # type: ignore[assignment]

        manager._evict_checkpoint(incoming_count=1000)

        assert len(manager.snapshots) == 1
        assert manager.snapshots[0] is s0

    def test_preserves_initial_snapshot(self):
        """Index 0 should never be evicted."""
        manager: SnapshotManager = SnapshotManager()
        s0 = _make_mock_snapshot(0)
        s1 = _make_mock_snapshot(100)
        manager.snapshots = [s0, s1]  # type: ignore[assignment]

        manager._evict_checkpoint(incoming_count=200)

        assert len(manager.snapshots) == 1
        assert manager.snapshots[0] is s0
        s1.terminate.assert_called_once()

    def test_evicts_from_dense_region(self):
        """Should evict from the densest cluster, not from the sparse gap."""
        manager: SnapshotManager = SnapshotManager()
        # [0, 100, 200, 300, 1000] — dense cluster at 100-300, sparse gap to 1000
        s0 = _make_mock_snapshot(0)
        s1 = _make_mock_snapshot(100)
        s2 = _make_mock_snapshot(200)
        s3 = _make_mock_snapshot(300)
        s4 = _make_mock_snapshot(1000)
        manager.snapshots = [s0, s1, s2, s3, s4]  # type: ignore[assignment]

        manager._evict_checkpoint(incoming_count=1100)

        assert len(manager.snapshots) == 4
        # s0 and s4 (endpoints) must survive; one of s1/s2/s3 is evicted
        assert manager.snapshots[0] is s0
        assert s4 in manager.snapshots
        # The evicted snapshot should be from the dense cluster
        evicted = [s for s in [s1, s2, s3] if s not in manager.snapshots]
        assert len(evicted) == 1
        evicted[0].terminate.assert_called_once()

    def test_equal_gaps_evicts_any(self):
        """When all gaps are equal, eviction should still work correctly."""
        manager: SnapshotManager = SnapshotManager()
        s0 = _make_mock_snapshot(0)
        s1 = _make_mock_snapshot(100)
        s2 = _make_mock_snapshot(200)
        manager.snapshots = [s0, s1, s2]  # type: ignore[assignment]

        manager._evict_checkpoint(incoming_count=300)

        assert len(manager.snapshots) == 2
        assert manager.snapshots[0] is s0  # initial preserved

    def test_avoids_evicting_checkpoint_near_large_gap(self):
        """Should not evict the checkpoint next to a large gap."""
        manager: SnapshotManager = SnapshotManager()
        # [0, 500, 600, 700] + incoming 800
        # Removing 500 would create gap 0→600 = 600 (bad)
        # Removing 600 would create gap 500→700 = 200 (good)
        # Removing 700 would create gap 600→800 = 200 (good)
        s0 = _make_mock_snapshot(0)
        s1 = _make_mock_snapshot(500)
        s2 = _make_mock_snapshot(600)
        s3 = _make_mock_snapshot(700)
        manager.snapshots = [s0, s1, s2, s3]  # type: ignore[assignment]

        manager._evict_checkpoint(incoming_count=800)

        assert len(manager.snapshots) == 3
        # s1 (500) should NOT be evicted — it guards the 0→500 gap
        assert s1 in manager.snapshots
        assert manager.snapshots[0] is s0


class TestCaptureSnapshotEviction:
    """Test that capture_snapshot evicts when at capacity."""

    @patch("dejaview.snapshots.snapshots.safe_fork", return_value=12345)
    def test_evicts_at_max_capacity(self, mock_fork: MagicMock):
        """When at max_checkpoints, capture should evict before adding."""
        manager: SnapshotManager = SnapshotManager(max_checkpoints=2)
        s0 = _make_mock_snapshot(0)
        s1 = _make_mock_snapshot(100)
        s0.snapshot_pid = 1000
        s1.snapshot_pid = 1001
        manager.snapshots = [s0, s1]  # type: ignore[assignment]

        manager.capture_snapshot(instruction_count=200)

        # One checkpoint should have been evicted
        assert len(manager.snapshots) == 2
        assert manager.snapshots[0] is s0  # initial preserved

    @patch("dejaview.snapshots.snapshots.safe_fork", return_value=12345)
    def test_no_eviction_below_capacity(self, mock_fork: MagicMock):
        """When below max_checkpoints, capture should not evict."""
        manager: SnapshotManager = SnapshotManager(max_checkpoints=5)
        s0 = _make_mock_snapshot(0)
        s0.snapshot_pid = 1000
        manager.snapshots = [s0]  # type: ignore[assignment]

        manager.capture_snapshot(instruction_count=100)

        assert len(manager.snapshots) == 2


class TestDejaViewImmediateCheckpoint:
    """Tests for immediate checkpoint capture in DejaView."""

    @patch("dejaview.snapshots.snapshots.safe_fork")
    def test_on_instruction_captures_at_interval(self, mock_fork):
        """_on_instruction should capture immediately when interval is reached."""
        mock_fork.return_value = 12345
        dv = DejaView(checkpoint_interval=100)
        dv.snapshot_manager._last_checkpoint_count = 0

        # Below interval - should not capture
        dv._on_instruction(50)
        assert len(dv.snapshot_manager.snapshots) == 0

        # At interval - should capture immediately
        dv._on_instruction(100)
        assert len(dv.snapshot_manager.snapshots) == 1
        assert dv.snapshot_manager.snapshots[0].info.instruction_count == 100

    def test_on_instruction_skipped_in_replay(self):
        """_on_instruction should never capture in a replay."""
        dv = DejaView(checkpoint_interval=100)
        dv.snapshot_manager.process_type = ProcessType.REPLAY
        dv.snapshot_manager._last_checkpoint_count = 0

        # Even at interval, should not capture in replay
        dv._on_instruction(200)
        assert len(dv.snapshot_manager.snapshots) == 0

    @patch("dejaview.snapshots.snapshots.safe_fork")
    def test_callback_invoked_on_line_event(self, mock_fork):
        """The checkpoint callback should be invoked on every line event."""
        mock_fork.return_value = 12345
        dv = DejaView(checkpoint_interval=50)

        # Simulate line events by calling the callback directly
        # (In real execution, FrameCounter calls this)
        for count in range(1, 101):
            dv._on_instruction(count)

        # Should have captured at counts 50 and 100
        assert len(dv.snapshot_manager.snapshots) == 2
        assert dv.snapshot_manager.snapshots[0].info.instruction_count == 50
        assert dv.snapshot_manager.snapshots[1].info.instruction_count == 100
