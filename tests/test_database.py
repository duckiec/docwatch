"""Tests for database.py — uses a temporary SQLite file per test."""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

import database


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_iso(dt: datetime | None = None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.isoformat()


def _future_iso(minutes: int = 120) -> str:
    return _utc_iso(datetime.now(timezone.utc) + timedelta(minutes=minutes))


def _past_iso(days: int = 5) -> str:
    return _utc_iso(datetime.now(timezone.utc) - timedelta(days=days))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect the DB to a per-test temp file."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(database, "DB_PATH", db_file)
    yield db_file


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

async def test_init_db_creates_tables(isolated_db):
    await database.init_db()
    async with database._connect() as db:
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
    assert "crashes" in tables
    assert "container_state" in tables
    assert "muted_containers" in tables


async def test_init_db_is_idempotent(isolated_db):
    # calling twice must not raise
    await database.init_db()
    await database.init_db()


# ---------------------------------------------------------------------------
# insert_crash / get_crash
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_ready(isolated_db):
    await database.init_db()


async def test_insert_and_get_crash(db_ready):
    record = {
        "container_name": "web",
        "container_id": "abc123",
        "timestamp": _utc_iso(),
        "exit_code": 1,
        "restart_count": 2,
        "uptime_seconds": 300,
        "crash_type": "Exit 1",
        "ai_summary": "Something went wrong.",
        "raw_logs": "ERROR: out of heap",
    }
    crash_id = await database.insert_crash(record)
    assert isinstance(crash_id, int)
    assert crash_id > 0

    fetched = await database.get_crash(crash_id)
    assert fetched is not None
    assert fetched["container_name"] == "web"
    assert fetched["exit_code"] == 1
    assert fetched["raw_logs"] == "ERROR: out of heap"


async def test_get_crash_not_found(db_ready):
    result = await database.get_crash(99999)
    assert result is None


# ---------------------------------------------------------------------------
# list_crashes
# ---------------------------------------------------------------------------

async def test_list_crashes_empty(db_ready):
    rows = await database.list_crashes()
    assert rows == []


async def test_list_crashes_returns_rows(db_ready):
    for i in range(3):
        await database.insert_crash({
            "container_name": f"svc{i}",
            "container_id": f"id{i}",
            "timestamp": _utc_iso(),
            "exit_code": 0,
            "restart_count": 0,
            "uptime_seconds": 60,
            "crash_type": "Clean exit",
            "ai_summary": None,
            "raw_logs": "",
        })
    rows = await database.list_crashes(limit=10)
    assert len(rows) == 3


async def test_list_crashes_filter_by_container(db_ready):
    await database.insert_crash({
        "container_name": "frontend",
        "container_id": "f1",
        "timestamp": _utc_iso(),
        "exit_code": 1,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "Exit 1",
        "ai_summary": None,
        "raw_logs": "",
    })
    await database.insert_crash({
        "container_name": "backend",
        "container_id": "b1",
        "timestamp": _utc_iso(),
        "exit_code": 1,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "Exit 1",
        "ai_summary": None,
        "raw_logs": "",
    })
    rows = await database.list_crashes(container="front")
    assert len(rows) == 1
    assert rows[0]["container_name"] == "frontend"


async def test_list_crashes_filter_by_crash_type(db_ready):
    await database.insert_crash({
        "container_name": "svc",
        "container_id": "s1",
        "timestamp": _utc_iso(),
        "exit_code": 137,
        "restart_count": 0,
        "uptime_seconds": 100,
        "crash_type": "OOM",
        "ai_summary": None,
        "raw_logs": "",
    })
    await database.insert_crash({
        "container_name": "svc",
        "container_id": "s2",
        "timestamp": _utc_iso(),
        "exit_code": 0,
        "restart_count": 0,
        "uptime_seconds": 100,
        "crash_type": "Clean exit",
        "ai_summary": None,
        "raw_logs": "",
    })
    rows = await database.list_crashes(crash_type="OOM")
    assert len(rows) == 1
    assert rows[0]["crash_type"] == "OOM"


async def test_list_crashes_pagination(db_ready):
    for i in range(5):
        await database.insert_crash({
            "container_name": "svc",
            "container_id": f"id{i}",
            "timestamp": _utc_iso(),
            "exit_code": 0,
            "restart_count": 0,
            "uptime_seconds": 10,
            "crash_type": "Clean exit",
            "ai_summary": None,
            "raw_logs": "",
        })
    page1 = await database.list_crashes(limit=3, offset=0)
    page2 = await database.list_crashes(limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) == 2


async def test_list_crashes_limit_clamped(db_ready):
    # limit > 200 should be clamped to 200 — just verify it runs without error
    rows = await database.list_crashes(limit=9999)
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# delete_crash
# ---------------------------------------------------------------------------

async def test_delete_crash_existing(db_ready):
    crash_id = await database.insert_crash({
        "container_name": "svc",
        "container_id": "id1",
        "timestamp": _utc_iso(),
        "exit_code": 1,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "Exit 1",
        "ai_summary": None,
        "raw_logs": "",
    })
    deleted = await database.delete_crash(crash_id)
    assert deleted is True
    assert await database.get_crash(crash_id) is None


async def test_delete_crash_nonexistent(db_ready):
    deleted = await database.delete_crash(99999)
    assert deleted is False


# ---------------------------------------------------------------------------
# delete_crashes
# ---------------------------------------------------------------------------

async def test_delete_crashes_all(db_ready):
    for i in range(4):
        await database.insert_crash({
            "container_name": "svc",
            "container_id": f"id{i}",
            "timestamp": _utc_iso(),
            "exit_code": 0,
            "restart_count": 0,
            "uptime_seconds": 10,
            "crash_type": "Clean exit",
            "ai_summary": None,
            "raw_logs": "",
        })
    count = await database.delete_crashes()
    assert count == 4
    assert await database.list_crashes() == []


async def test_delete_crashes_by_container(db_ready):
    for name in ["alpha", "alpha", "beta"]:
        await database.insert_crash({
            "container_name": name,
            "container_id": name,
            "timestamp": _utc_iso(),
            "exit_code": 0,
            "restart_count": 0,
            "uptime_seconds": 10,
            "crash_type": "Clean exit",
            "ai_summary": None,
            "raw_logs": "",
        })
    count = await database.delete_crashes(container="alpha")
    assert count == 2
    remaining = await database.list_crashes()
    assert all(r["container_name"] == "beta" for r in remaining)


async def test_delete_crashes_by_type(db_ready):
    await database.insert_crash({
        "container_name": "svc",
        "container_id": "s1",
        "timestamp": _utc_iso(),
        "exit_code": 137,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "OOM",
        "ai_summary": None,
        "raw_logs": "",
    })
    await database.insert_crash({
        "container_name": "svc",
        "container_id": "s2",
        "timestamp": _utc_iso(),
        "exit_code": 0,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "Clean exit",
        "ai_summary": None,
        "raw_logs": "",
    })
    count = await database.delete_crashes(crash_type="OOM")
    assert count == 1
    remaining = await database.list_crashes()
    assert remaining[0]["crash_type"] == "Clean exit"


# ---------------------------------------------------------------------------
# delete_old_crashes
# ---------------------------------------------------------------------------

async def test_delete_old_crashes_removes_old(db_ready):
    old_ts = _past_iso(days=10)
    new_ts = _utc_iso()
    await database.insert_crash({
        "container_name": "svc",
        "container_id": "old",
        "timestamp": old_ts,
        "exit_code": 1,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "Exit 1",
        "ai_summary": None,
        "raw_logs": "",
    })
    await database.insert_crash({
        "container_name": "svc",
        "container_id": "new",
        "timestamp": new_ts,
        "exit_code": 1,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "Exit 1",
        "ai_summary": None,
        "raw_logs": "",
    })
    deleted = await database.delete_old_crashes(older_than_days=5)
    assert deleted == 1
    remaining = await database.list_crashes()
    assert len(remaining) == 1


async def test_delete_old_crashes_zero_days_noop(db_ready):
    await database.insert_crash({
        "container_name": "svc",
        "container_id": "c1",
        "timestamp": _past_iso(days=100),
        "exit_code": 1,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "Exit 1",
        "ai_summary": None,
        "raw_logs": "",
    })
    deleted = await database.delete_old_crashes(0)
    assert deleted == 0


# ---------------------------------------------------------------------------
# get_crash_type_counts
# ---------------------------------------------------------------------------

async def test_get_crash_type_counts(db_ready):
    for crash_type in ["OOM", "OOM", "Exit 1"]:
        await database.insert_crash({
            "container_name": "svc",
            "container_id": crash_type,
            "timestamp": _utc_iso(),
            "exit_code": 137,
            "restart_count": 0,
            "uptime_seconds": 10,
            "crash_type": crash_type,
            "ai_summary": None,
            "raw_logs": "",
        })
    rows = await database.get_crash_type_counts(limit=10)
    types = {r["crash_type"]: r["count"] for r in rows}
    assert types["OOM"] == 2
    assert types["Exit 1"] == 1


async def test_get_crash_type_counts_empty(db_ready):
    rows = await database.get_crash_type_counts()
    assert rows == []


# ---------------------------------------------------------------------------
# get_timeline
# ---------------------------------------------------------------------------

async def test_get_timeline_returns_list(db_ready):
    await database.insert_crash({
        "container_name": "svc",
        "container_id": "c1",
        "timestamp": _utc_iso(),
        "exit_code": 1,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "Exit 1",
        "ai_summary": None,
        "raw_logs": "",
    })
    rows = await database.get_timeline(hours=24)
    assert isinstance(rows, list)
    assert len(rows) >= 1
    assert "bucket" in rows[0]
    assert "count" in rows[0]


async def test_get_timeline_hours_clamped(db_ready):
    rows = await database.get_timeline(hours=9999)
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# get_crashes_for_export
# ---------------------------------------------------------------------------

async def test_get_crashes_for_export(db_ready):
    await database.insert_crash({
        "container_name": "svc",
        "container_id": "c1",
        "timestamp": _utc_iso(),
        "exit_code": 0,
        "restart_count": 0,
        "uptime_seconds": 10,
        "crash_type": "Clean exit",
        "ai_summary": "ok",
        "raw_logs": "done",
    })
    rows = await database.get_crashes_for_export(limit=10)
    assert len(rows) == 1
    assert "raw_logs" in rows[0]


# ---------------------------------------------------------------------------
# container_state
# ---------------------------------------------------------------------------

async def test_get_container_state_not_found(db_ready):
    result = await database.get_container_state("nonexistent")
    assert result is None


async def test_upsert_and_get_container_state(db_ready):
    await database.upsert_container_state("cid1", "web", 3, "running")
    state = await database.get_container_state("cid1")
    assert state is not None
    assert state["container_name"] == "web"
    assert state["last_restart_count"] == 3
    assert state["last_status"] == "running"


async def test_upsert_container_state_updates(db_ready):
    await database.upsert_container_state("cid1", "web", 1, "running")
    await database.upsert_container_state("cid1", "web", 5, "exited")
    state = await database.get_container_state("cid1")
    assert state["last_restart_count"] == 5
    assert state["last_status"] == "exited"


# ---------------------------------------------------------------------------
# muted_containers
# ---------------------------------------------------------------------------

async def test_set_and_check_mute(db_ready):
    await database.set_container_mute("web", _future_iso(120), "maintenance")
    assert await database.is_container_muted("web") is True


async def test_is_container_not_muted_absent(db_ready):
    assert await database.is_container_muted("non-existent") is False


async def test_is_container_not_muted_expired(db_ready):
    past = _utc_iso(datetime.now(timezone.utc) - timedelta(minutes=1))
    await database.set_container_mute("web", past)
    assert await database.is_container_muted("web") is False


async def test_clear_container_mute(db_ready):
    await database.set_container_mute("web", _future_iso(60))
    removed = await database.clear_container_mute("web")
    assert removed is True
    assert await database.is_container_muted("web") is False


async def test_clear_container_mute_nonexistent(db_ready):
    removed = await database.clear_container_mute("does-not-exist")
    assert removed is False


async def test_list_container_mutes_active_only(db_ready):
    await database.set_container_mute("alpha", _future_iso(60), "planned")
    await database.set_container_mute("beta", _utc_iso(datetime.now(timezone.utc) - timedelta(minutes=5)))
    mutes = await database.list_container_mutes()
    names = [m["container_name"] for m in mutes]
    assert "alpha" in names
    assert "beta" not in names


async def test_set_container_mute_upserts(db_ready):
    await database.set_container_mute("web", _future_iso(60), "reason A")
    await database.set_container_mute("web", _future_iso(120), "reason B")
    mutes = await database.list_container_mutes()
    assert len(mutes) == 1
    assert mutes[0]["reason"] == "reason B"


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

async def test_get_stats_empty(db_ready):
    stats = await database.get_stats()
    assert stats["crashes_today"] == 0
    assert stats["most_affected_container"] is None
    assert stats["most_common_crash_type"] is None
    assert stats["last_crash_time"] is None


async def test_get_stats_with_data(db_ready):
    today = datetime.now(timezone.utc).date().isoformat()
    for i in range(3):
        await database.insert_crash({
            "container_name": "web",
            "container_id": f"id{i}",
            "timestamp": f"{today}T10:00:0{i}+00:00",
            "exit_code": 1,
            "restart_count": 0,
            "uptime_seconds": 10,
            "crash_type": "Exit 1",
            "ai_summary": None,
            "raw_logs": "",
        })
    stats = await database.get_stats()
    assert stats["crashes_today"] == 3
    assert stats["most_affected_container"] == "web"
    assert stats["most_common_crash_type"] == "Exit 1"
    assert stats["last_crash_time"] is not None


# ---------------------------------------------------------------------------
# _execute_with_retry
# ---------------------------------------------------------------------------

async def test_execute_with_retry_succeeds_first_attempt():
    call_count = 0

    async def _op():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await database._execute_with_retry(_op)
    assert result == "ok"
    assert call_count == 1


async def test_execute_with_retry_on_locked_db(monkeypatch):
    """Simulate a transient 'database is locked' error that resolves on retry."""
    import aiosqlite

    call_count = 0

    async def _op():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise aiosqlite.OperationalError("database is locked")
        return "recovered"

    monkeypatch.setattr(database, "DB_LOCK_RETRY_COUNT", 3)
    monkeypatch.setattr(database, "DB_LOCK_RETRY_DELAY_SECONDS", 0.0)

    result = await database._execute_with_retry(_op)
    assert result == "recovered"
    assert call_count == 2


async def test_execute_with_retry_raises_non_lock_error():
    import aiosqlite

    async def _op():
        raise aiosqlite.OperationalError("table does not exist")

    with pytest.raises(aiosqlite.OperationalError, match="table does not exist"):
        await database._execute_with_retry(_op)


async def test_execute_with_retry_exhausts_retries(monkeypatch):
    import aiosqlite

    monkeypatch.setattr(database, "DB_LOCK_RETRY_COUNT", 2)
    monkeypatch.setattr(database, "DB_LOCK_RETRY_DELAY_SECONDS", 0.0)

    async def _op():
        raise aiosqlite.OperationalError("database is locked")

    with pytest.raises(aiosqlite.OperationalError):
        await database._execute_with_retry(_op)
