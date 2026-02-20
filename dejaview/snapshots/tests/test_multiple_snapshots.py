"""Tests for multiple snapshots functionality."""

from unittest.mock import MagicMock, patch

import pytest

from dejaview.counting.dejaview import DejaView
from dejaview.snapshots.snapshots import (
    ProcessType,
    SnapshotInfo,
    SnapshotManager,
)


def _make_line_event(count: int) -> MagicMock:
    """Create a mock line Event for testing _on_instruction."""
    event = MagicMock()
    event.count = count
    event.event = "line"
    return event


def _make_mock_snapshot(instruction_count: int) -> MagicMock:
    """Create a mock _Snapshot with the given instruction count."""
    mock = MagicMock()
    mock.info = SnapshotInfo(instruction_count=instruction_count)
    return mock


class TestFindBestSnapshot:
    """Tests for find_best_snapshot using bisect."""

    def test_empty_snapshots_returns_zero(self):
        """With no snapshots, should return 0."""
        manager: SnapshotManager = SnapshotManager()
        assert manager.find_best_snapshot(100) == 0

    def test_exact_match(self):
        """Should return the index of exact match."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [
            _make_mock_snapshot(0),
            _make_mock_snapshot(100),
            _make_mock_snapshot(200),
        ]  # type: ignore[assignment]
        assert manager.find_best_snapshot(100) == 1

    def test_between_snapshots_picks_earlier(self):
        """Should return the index of the snapshot before target."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [
            _make_mock_snapshot(0),
            _make_mock_snapshot(100),
            _make_mock_snapshot(200),
        ]  # type: ignore[assignment]
        assert manager.find_best_snapshot(150) == 1  # 100 is before 150

    def test_target_before_all_snapshots(self):
        """Should raise if target is before all snapshots (initial snapshot killed)."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [
            _make_mock_snapshot(100),
            _make_mock_snapshot(200),
        ]  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="initial snapshot"):
            manager.find_best_snapshot(50)

    def test_target_after_all_snapshots(self):
        """Should return the last index when target is after all snapshots."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [
            _make_mock_snapshot(0),
            _make_mock_snapshot(100),
            _make_mock_snapshot(200),
        ]  # type: ignore[assignment]
        assert manager.find_best_snapshot(300) == 2

    def test_single_snapshot(self):
        """With single snapshot, return 0 for targets at or after; raise for before."""
        manager: SnapshotManager = SnapshotManager()
        manager.snapshots = [_make_mock_snapshot(100)]  # type: ignore[assignment]
        assert manager.find_best_snapshot(100) == 0
        assert manager.find_best_snapshot(150) == 0
        with pytest.raises(RuntimeError, match="initial snapshot"):
            manager.find_best_snapshot(50)


class TestEvictSnapshot:
    """Tests for min-max-gap eviction policy."""

    def test_evict_noop_with_single_snapshot(self):
        """Eviction should do nothing with only the initial snapshot."""
        manager: SnapshotManager = SnapshotManager()
        s0 = _make_mock_snapshot(0)
        manager.snapshots = [s0]  # type: ignore[assignment]

        manager._evict_snapshot(incoming_count=1000)

        assert len(manager.snapshots) == 1
        assert manager.snapshots[0] is s0

    def test_preserves_initial_snapshot(self):
        """Index 0 should never be evicted."""
        manager: SnapshotManager = SnapshotManager()
        s0 = _make_mock_snapshot(0)
        s1 = _make_mock_snapshot(100)
        manager.snapshots = [s0, s1]  # type: ignore[assignment]

        manager._evict_snapshot(incoming_count=200)

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

        manager._evict_snapshot(incoming_count=1100)

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

        manager._evict_snapshot(incoming_count=300)

        assert len(manager.snapshots) == 2
        assert manager.snapshots[0] is s0  # initial preserved

    def test_avoids_evicting_snapshot_near_large_gap(self):
        """Should not evict the snapshot next to a large gap."""
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

        manager._evict_snapshot(incoming_count=800)

        assert len(manager.snapshots) == 3
        # s1 (500) should NOT be evicted — it guards the 0→500 gap
        assert s1 in manager.snapshots
        assert manager.snapshots[0] is s0


class TestCaptureSnapshotEviction:
    """Test that capture_snapshot evicts when at capacity."""

    @patch("dejaview.snapshots.snapshots.safe_fork", return_value=12345)
    def test_evicts_at_max_capacity(self, mock_fork: MagicMock):
        """When at max_snapshots, capture should evict before adding."""
        manager: SnapshotManager = SnapshotManager(max_snapshots=2)
        s0 = _make_mock_snapshot(0)
        s1 = _make_mock_snapshot(100)
        s0.snapshot_pid = 1000
        s1.snapshot_pid = 1001
        manager.snapshots = [s0, s1]  # type: ignore[assignment]

        manager.capture_snapshot(instruction_count=200)

        # One snapshot should have been evicted
        assert len(manager.snapshots) == 2
        assert manager.snapshots[0] is s0  # initial preserved

    @patch("dejaview.snapshots.snapshots.safe_fork", return_value=12345)
    def test_no_eviction_below_capacity(self, mock_fork: MagicMock):
        """When below max_snapshots, capture should not evict."""
        manager: SnapshotManager = SnapshotManager(max_snapshots=5)
        s0 = _make_mock_snapshot(0)
        s0.snapshot_pid = 1000
        manager.snapshots = [s0]  # type: ignore[assignment]

        manager.capture_snapshot(instruction_count=100)

        assert len(manager.snapshots) == 2


class TestDejaViewSnapshotHandler:
    """Tests for snapshot capture handler in DejaView."""

    @patch("dejaview.snapshots.snapshots.safe_fork")
    def test_on_instruction_captures_at_interval(self, mock_fork):
        """_on_instruction should capture immediately when interval is reached."""
        mock_fork.return_value = 12345
        dv = DejaView(snapshot_interval=100)
        dv.snapshot_manager._last_snapshot_count = 0

        # Below interval - should not capture
        dv._on_instruction(_make_line_event(50))
        assert len(dv.snapshot_manager.snapshots) == 0

        # At interval - should capture immediately
        dv._on_instruction(_make_line_event(100))
        assert len(dv.snapshot_manager.snapshots) == 1
        assert dv.snapshot_manager.snapshots[0].info.instruction_count == 100

    def test_on_instruction_skipped_in_replay(self):
        """_on_instruction should never capture in a replay."""
        dv = DejaView(snapshot_interval=100)
        dv.snapshot_manager.process_type = ProcessType.REPLAY
        dv.snapshot_manager._last_snapshot_count = 0

        # Even at interval, should not capture in replay
        dv._on_instruction(_make_line_event(200))
        assert len(dv.snapshot_manager.snapshots) == 0

    @patch("dejaview.snapshots.snapshots.safe_fork")
    def test_handler_captures_multiple_snapshots(self, mock_fork):
        """The handler should capture at each interval threshold."""
        mock_fork.return_value = 12345
        dv = DejaView(snapshot_interval=50)

        # Simulate line events by calling the handler directly
        for count in range(1, 101):
            dv._on_instruction(_make_line_event(count))

        # Should have captured at counts 50 and 100
        assert len(dv.snapshot_manager.snapshots) == 2
        assert dv.snapshot_manager.snapshots[0].info.instruction_count == 50
        assert dv.snapshot_manager.snapshots[1].info.instruction_count == 100

    def test_on_instruction_ignores_non_line_events(self):
        """_on_instruction should only act on line events."""
        dv = DejaView(snapshot_interval=1)
        dv.snapshot_manager._last_snapshot_count = 0

        # Call and return events should be ignored
        call_event = MagicMock(count=100, event="call")
        return_event = MagicMock(count=200, event="return")
        assert dv._on_instruction(call_event) is False
        assert dv._on_instruction(return_event) is False
        assert len(dv.snapshot_manager.snapshots) == 0


class TestSkipWastefulCapture:
    """Tests for the skip-capture-if-wasteful optimization."""

    @patch("dejaview.snapshots.snapshots.safe_fork", return_value=12345)
    def test_skips_when_new_gap_is_smallest(self, mock_fork):
        """Should skip capture when new snapshot is in the densest region."""
        manager: SnapshotManager = SnapshotManager(max_snapshots=3)
        s0 = _make_mock_snapshot(0)
        s1 = _make_mock_snapshot(500)
        s2 = _make_mock_snapshot(1000)
        s0.snapshot_pid = 1000
        s1.snapshot_pid = 1001
        s2.snapshot_pid = 1002
        manager.snapshots = [s0, s1, s2]  # type: ignore[assignment]

        # Gap from s2 (1000) to new (1100) = 100
        # Existing gaps: 0->500 = 500, 500->1000 = 500
        # New gap (100) is smallest, so skip
        result = manager.capture_snapshot(instruction_count=1100)

        assert result is None
        assert len(manager.snapshots) == 3  # No change
        mock_fork.assert_not_called()

    @patch("dejaview.snapshots.snapshots.safe_fork", return_value=12345)
    def test_does_not_skip_when_existing_gap_is_smaller(self, mock_fork):
        """Should proceed with capture when an existing gap is smaller."""
        manager: SnapshotManager = SnapshotManager(max_snapshots=3)
        s0 = _make_mock_snapshot(0)
        s1 = _make_mock_snapshot(10)
        s2 = _make_mock_snapshot(1000)
        s0.snapshot_pid = 1000
        s1.snapshot_pid = 1001
        s2.snapshot_pid = 1002
        manager.snapshots = [s0, s1, s2]  # type: ignore[assignment]

        # Gap from s2 (1000) to new (1500) = 500
        # Existing gap 0->10 = 10 is smaller, so don't skip
        manager.capture_snapshot(instruction_count=1500)

        # Should have evicted and captured (fork was called)
        mock_fork.assert_called_once()
