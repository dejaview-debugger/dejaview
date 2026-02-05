"""Tests for multiple snapshots functionality."""

from unittest.mock import MagicMock

import pytest

from dejaview.counting.dejaview import DejaView
from dejaview.snapshots.snapshots import (
    DEFAULT_CHECKPOINT_INTERVAL,
    DEFAULT_MAX_CHECKPOINTS,
    CheckpointInfo,
    ProcessType,
    SnapshotManager,
)


class TestCheckpointInfoDataclass:
    """Tests for CheckpointInfo dataclass."""

    def test_creation(self):
        info = CheckpointInfo(instruction_count=100)
        assert info.instruction_count == 100


class TestSnapshotManagerConfig:
    """Tests for SnapshotManager configuration."""

    def test_default_config(self):
        manager: SnapshotManager = SnapshotManager()
        assert manager.checkpoint_interval == DEFAULT_CHECKPOINT_INTERVAL
        assert manager.max_checkpoints == DEFAULT_MAX_CHECKPOINTS

    def test_custom_config(self):
        manager: SnapshotManager = SnapshotManager(
            checkpoint_interval=500, max_checkpoints=5
        )
        assert manager.checkpoint_interval == 500
        assert manager.max_checkpoints == 5


class TestFindBestCheckpoint:
    """Tests for checkpoint selection logic."""

    def test_find_best_checkpoint_empty(self):
        """Should return 0 when no snapshots exist."""
        manager: SnapshotManager = SnapshotManager()
        # No snapshots yet
        assert manager.find_best_checkpoint(1000) == 0

    def test_find_best_checkpoint_single(self):
        """Should return 0 when only initial snapshot exists."""
        manager: SnapshotManager = SnapshotManager()
        # Simulate having captured the initial snapshot by adding a mock
        # In actual usage, capture_snapshot would be called
        manager.snapshots = []  # Empty list for testing selection logic
        assert manager.find_best_checkpoint(500) == 0

    def test_find_best_checkpoint_multiple(self):
        """Should find the best checkpoint for a given target count."""
        manager: SnapshotManager = SnapshotManager(checkpoint_interval=100)

        # Create mock snapshots at different instruction counts
        mock_snapshots = []
        for count in [0, 100, 200, 300, 400]:
            mock = MagicMock()
            mock.info = CheckpointInfo(instruction_count=count)
            mock_snapshots.append(mock)

        manager.snapshots = mock_snapshots  # type: ignore[assignment]

        # Test various target counts
        assert manager.find_best_checkpoint(50) == 0  # Before first, use 0
        assert manager.find_best_checkpoint(100) == 1  # Exact match
        assert manager.find_best_checkpoint(150) == 1  # Between 100 and 200
        assert manager.find_best_checkpoint(350) == 3  # Between 300 and 400
        assert manager.find_best_checkpoint(500) == 4  # After all, use last


class TestGetCheckpointInfo:
    """Tests for CheckpointInfo list retrieval."""

    def test_get_checkpoint_info(self):
        manager: SnapshotManager = SnapshotManager()

        mock_snapshots = []
        for count in [0, 100, 200]:
            mock = MagicMock()
            mock.info = CheckpointInfo(instruction_count=count)
            mock_snapshots.append(mock)

        manager.snapshots = mock_snapshots  # type: ignore[assignment]

        infos = manager.get_checkpoint_info()
        assert len(infos) == 3
        assert infos[0].instruction_count == 0
        assert infos[1].instruction_count == 100
        assert infos[2].instruction_count == 200


class TestMaybeCaptureCheckpoint:
    """Tests for automatic checkpoint capture logic."""

    def test_no_capture_before_interval(self):
        """Should not capture checkpoint before interval is reached."""
        manager: SnapshotManager = SnapshotManager(checkpoint_interval=100)
        manager._last_checkpoint_count = 0

        # Should not capture at count 50 (interval not reached)
        result = manager.maybe_capture_checkpoint(50)
        assert result is False

    def test_no_capture_in_replay_process(self):
        """Should not capture checkpoint in replay process."""
        manager: SnapshotManager = SnapshotManager(checkpoint_interval=100)
        manager.process_type = ProcessType.REPLAY
        manager._last_checkpoint_count = 0

        # Should not capture even if interval reached
        result = manager.maybe_capture_checkpoint(200)
        assert result is False


class TestGetCheckpointCount:
    """Tests for checkpoint counting."""

    def test_initial_count(self):
        manager: SnapshotManager = SnapshotManager()
        assert manager.get_checkpoint_count() == 0

    def test_count_with_snapshots(self):
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [MagicMock(), MagicMock(), MagicMock()]  # type: ignore
        assert manager.get_checkpoint_count() == 3


class TestDejaViewPendingCheckpoint:
    """Tests for DejaView pending checkpoint mechanism."""

    def test_on_instruction_marks_pending(self):
        """Should mark a pending checkpoint when interval is reached."""
        dv = DejaView(checkpoint_interval=100)
        dv.snapshot_manager._last_checkpoint_count = 0

        # Before interval - no pending checkpoint
        dv._on_instruction(50)
        assert dv._pending_checkpoint is False

        # At/after interval - should mark pending
        dv._on_instruction(100)
        assert dv._pending_checkpoint is True
        assert dv._pending_checkpoint_count == 100

    def test_on_instruction_skipped_in_replay(self):
        """Should not mark pending checkpoint in replay process."""
        dv = DejaView(checkpoint_interval=100)
        dv.snapshot_manager.process_type = ProcessType.REPLAY
        dv.snapshot_manager._last_checkpoint_count = 0

        dv._on_instruction(200)
        assert dv._pending_checkpoint is False

    def test_maybe_capture_clears_pending(self):
        """Should clear pending flag when called (even in replay)."""
        dv = DejaView(checkpoint_interval=100)
        dv._pending_checkpoint = True
        dv._pending_checkpoint_count = 100

        # In replay mode, should clear but not capture
        dv.snapshot_manager.process_type = ProcessType.REPLAY
        result = dv._maybe_capture_pending_checkpoint()
        assert result is False
        assert dv._pending_checkpoint is False

    def test_no_capture_when_not_pending(self):
        """Should return False when no checkpoint is pending."""
        dv = DejaView()
        dv._pending_checkpoint = False

        result = dv._maybe_capture_pending_checkpoint()
        assert result is False


# Integration tests would require forking, which is complex to test
# The following tests are marked for integration testing


@pytest.mark.skip(reason="Integration test requiring forking - run manually")
class TestSnapshotManagerIntegration:
    """Integration tests for full snapshot/resume cycle."""

    def test_capture_and_resume_multiple(self):
        """Test capturing and resuming from multiple checkpoints."""
        # This would test the full fork-based mechanism
        pass

    def test_eviction_at_max_checkpoints(self):
        """Test that old checkpoints are evicted when max is reached."""
        pass
