"""Tests for watcher.py utility functions and retention logic."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import watcher


# ---------------------------------------------------------------------------
# _get_int_env
# ---------------------------------------------------------------------------

class TestGetIntEnv:
    def test_valid_value(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT", "42")
        result = watcher._get_int_env("TEST_ENV_INT", default=10, minimum=1)
        assert result == 42

    def test_uses_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_ENV_INT", raising=False)
        result = watcher._get_int_env("TEST_ENV_INT", default=7, minimum=1)
        assert result == 7

    def test_clamps_to_minimum(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT", "0")
        result = watcher._get_int_env("TEST_ENV_INT", default=10, minimum=5)
        assert result == 5

    def test_invalid_value_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT", "notanumber")
        result = watcher._get_int_env("TEST_ENV_INT", default=99, minimum=1)
        assert result == 99

    def test_negative_env_clamped(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT", "-100")
        result = watcher._get_int_env("TEST_ENV_INT", default=5, minimum=1)
        assert result == 1

    def test_exactly_at_minimum(self, monkeypatch):
        monkeypatch.setenv("TEST_ENV_INT", "5")
        result = watcher._get_int_env("TEST_ENV_INT", default=10, minimum=5)
        assert result == 5


# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------

class TestSafeInt:
    def test_integer_input(self):
        assert watcher._safe_int(42) == 42

    def test_string_integer(self):
        assert watcher._safe_int("7") == 7

    def test_none_returns_default(self):
        assert watcher._safe_int(None) == 0

    def test_float_truncates(self):
        assert watcher._safe_int(3.9) == 3

    def test_empty_string_returns_default(self):
        assert watcher._safe_int("") == 0

    def test_custom_default(self):
        assert watcher._safe_int("abc", default=99) == 99

    def test_zero(self):
        assert watcher._safe_int(0) == 0


# ---------------------------------------------------------------------------
# _parse_uptime_seconds
# ---------------------------------------------------------------------------

class TestParseUptimeSeconds:
    def _attrs(self, started_at=None, finished_at=None):
        state = {}
        if started_at is not None:
            state["StartedAt"] = started_at
        if finished_at is not None:
            state["FinishedAt"] = finished_at
        return {"State": state}

    def test_no_started_at_returns_none(self):
        result = watcher._parse_uptime_seconds({})
        assert result is None

    def test_no_state_returns_none(self):
        result = watcher._parse_uptime_seconds({"State": {}})
        assert result is None

    def test_finished_at_zero_value_uses_now(self):
        # zero finished_at means container is still running
        started = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
        attrs = self._attrs(started_at=started, finished_at="0001-01-01T00:00:00Z")
        result = watcher._parse_uptime_seconds(attrs)
        assert result is not None
        assert result >= 119  # at least 119 seconds

    def test_finished_at_set(self):
        started = "2024-01-01T10:00:00Z"
        finished = "2024-01-01T10:05:30Z"
        attrs = self._attrs(started_at=started, finished_at=finished)
        result = watcher._parse_uptime_seconds(attrs)
        assert result == 330  # 5 min 30 sec

    def test_no_finished_at_uses_now(self):
        started = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        attrs = self._attrs(started_at=started)
        result = watcher._parse_uptime_seconds(attrs)
        assert result is not None
        assert result >= 59

    def test_uptime_never_negative(self):
        # finished_at before started_at → max(0, negative) == 0
        started = "2024-01-01T10:05:00Z"
        finished = "2024-01-01T10:00:00Z"
        attrs = self._attrs(started_at=started, finished_at=finished)
        result = watcher._parse_uptime_seconds(attrs)
        assert result == 0

    def test_invalid_date_format_returns_none(self):
        attrs = self._attrs(started_at="not-a-date", finished_at="also-not")
        result = watcher._parse_uptime_seconds(attrs)
        assert result is None


# ---------------------------------------------------------------------------
# _truncate_logs
# ---------------------------------------------------------------------------

class TestTruncateLogs:
    def test_empty_string(self):
        assert watcher._truncate_logs("", 10) == ""

    def test_none_returns_empty(self):
        assert watcher._truncate_logs(None, 10) == ""

    def test_within_limit_unchanged(self):
        logs = "line1\nline2\nline3"
        result = watcher._truncate_logs(logs, 10)
        assert result == logs

    def test_exactly_at_limit(self):
        logs = "\n".join(f"line{i}" for i in range(5))
        result = watcher._truncate_logs(logs, 5)
        assert result == logs

    def test_truncates_to_last_n_lines(self):
        lines = [f"line{i}" for i in range(10)]
        logs = "\n".join(lines)
        result = watcher._truncate_logs(logs, 3)
        assert result == "line7\nline8\nline9"

    def test_single_long_line(self):
        logs = "a" * 1000
        result = watcher._truncate_logs(logs, 5)
        assert result == logs  # single line, no truncation needed

    def test_max_lines_one(self):
        logs = "first\nsecond\nthird"
        result = watcher._truncate_logs(logs, 1)
        assert result == "third"


# ---------------------------------------------------------------------------
# get_last_error
# ---------------------------------------------------------------------------

def test_get_last_error_initial():
    # Module-level _last_error may vary, just ensure it returns str or None
    result = watcher.get_last_error()
    assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# _maybe_run_retention_cleanup
# ---------------------------------------------------------------------------

async def test_retention_cleanup_skipped_when_days_zero(monkeypatch):
    monkeypatch.setattr(watcher, "CRASH_RETENTION_DAYS", 0)
    monkeypatch.setattr(watcher, "_last_retention_sweep", None)

    with patch.object(watcher.database, "delete_old_crashes", new_callable=AsyncMock) as mock_delete:
        await watcher._maybe_run_retention_cleanup()
        mock_delete.assert_not_called()


async def test_retention_cleanup_runs_when_due(monkeypatch, tmp_path):
    monkeypatch.setattr(watcher, "CRASH_RETENTION_DAYS", 30)
    monkeypatch.setattr(watcher, "_last_retention_sweep", None)

    with patch.object(watcher.database, "delete_old_crashes", new_callable=AsyncMock, return_value=5) as mock_delete:
        await watcher._maybe_run_retention_cleanup()
        mock_delete.assert_awaited_once_with(30)


async def test_retention_cleanup_skipped_when_recently_run(monkeypatch):
    monkeypatch.setattr(watcher, "CRASH_RETENTION_DAYS", 30)
    monkeypatch.setattr(watcher, "RETENTION_SWEEP_MINUTES", 60)
    monkeypatch.setattr(watcher, "_last_retention_sweep",
                        datetime.now(timezone.utc) - timedelta(minutes=10))

    with patch.object(watcher.database, "delete_old_crashes", new_callable=AsyncMock) as mock_delete:
        await watcher._maybe_run_retention_cleanup()
        mock_delete.assert_not_called()


async def test_retention_cleanup_runs_after_interval(monkeypatch):
    monkeypatch.setattr(watcher, "CRASH_RETENTION_DAYS", 30)
    monkeypatch.setattr(watcher, "RETENTION_SWEEP_MINUTES", 60)
    monkeypatch.setattr(watcher, "_last_retention_sweep",
                        datetime.now(timezone.utc) - timedelta(hours=2))

    with patch.object(watcher.database, "delete_old_crashes", new_callable=AsyncMock, return_value=0) as mock_delete:
        await watcher._maybe_run_retention_cleanup()
        mock_delete.assert_awaited_once()


async def test_retention_cleanup_exception_is_swallowed(monkeypatch):
    monkeypatch.setattr(watcher, "CRASH_RETENTION_DAYS", 30)
    monkeypatch.setattr(watcher, "_last_retention_sweep", None)

    with patch.object(watcher.database, "delete_old_crashes", new_callable=AsyncMock, side_effect=RuntimeError("db error")):
        # Should not raise
        await watcher._maybe_run_retention_cleanup()


# ---------------------------------------------------------------------------
# start_watcher / stop_watcher
# ---------------------------------------------------------------------------

def test_start_watcher_does_not_double_start():
    """If scheduler is already running, start_watcher should be a no-op."""
    mock_scheduler = MagicMock()
    mock_scheduler.running = True

    with patch.object(watcher, "scheduler", mock_scheduler):
        watcher.start_watcher()
        mock_scheduler.add_job.assert_not_called()
        mock_scheduler.start.assert_not_called()


def test_start_watcher_starts_scheduler():
    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch.object(watcher, "scheduler", mock_scheduler):
        watcher.start_watcher()
        mock_scheduler.add_job.assert_called_once()
        mock_scheduler.start.assert_called_once()


def test_stop_watcher_shuts_down():
    mock_scheduler = MagicMock()
    mock_scheduler.running = True

    with patch.object(watcher, "scheduler", mock_scheduler):
        watcher.stop_watcher()
        mock_scheduler.shutdown.assert_called_once_with(wait=False)


def test_stop_watcher_noop_when_not_running():
    mock_scheduler = MagicMock()
    mock_scheduler.running = False

    with patch.object(watcher, "scheduler", mock_scheduler):
        watcher.stop_watcher()
        mock_scheduler.shutdown.assert_not_called()
