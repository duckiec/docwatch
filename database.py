from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

DB_PATH = os.getenv("DB_PATH", "data/docwatch.db")
DB_TIMEOUT_SECONDS = float(os.getenv("DB_TIMEOUT_SECONDS", "10"))
DB_LOCK_RETRY_COUNT = int(os.getenv("DB_LOCK_RETRY_COUNT", "3"))
DB_LOCK_RETRY_DELAY_SECONDS = float(os.getenv("DB_LOCK_RETRY_DELAY_SECONDS", "0.15"))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_db_dir() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def _connect() -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=5000;")
        yield db


async def _execute_with_retry(fn):
    last_exc = None
    for attempt in range(DB_LOCK_RETRY_COUNT + 1):
        try:
            return await fn()
        except aiosqlite.OperationalError as exc:
            last_exc = exc
            if "database is locked" not in str(exc).lower() or attempt >= DB_LOCK_RETRY_COUNT:
                raise
            await asyncio.sleep(DB_LOCK_RETRY_DELAY_SECONDS)
    raise last_exc  # pragma: no cover


async def init_db() -> None:
    _ensure_db_dir()
    async with _connect() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS crashes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              container_name TEXT,
              container_id TEXT,
              timestamp TEXT,
              exit_code INTEGER,
              restart_count INTEGER,
              uptime_seconds INTEGER,
              crash_type TEXT,
              ai_summary TEXT,
                            raw_logs TEXT,
                            acknowledged_at TEXT
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS container_state (
              container_id TEXT PRIMARY KEY,
              container_name TEXT,
              last_restart_count INTEGER,
              last_status TEXT,
              updated_at TEXT
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS muted_containers (
                container_name TEXT PRIMARY KEY,
                muted_until TEXT,
                reason TEXT,
                updated_at TEXT
            );
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_crashes_timestamp ON crashes(timestamp DESC);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_crashes_container ON crashes(container_name);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_crashes_type ON crashes(crash_type);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_crashes_acknowledged ON crashes(acknowledged_at);")
        try:
            await db.execute("ALTER TABLE crashes ADD COLUMN acknowledged_at TEXT;")
        except aiosqlite.OperationalError:
            pass
        await db.commit()


async def get_container_state(container_id: str) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT container_id, container_name, last_restart_count, last_status, updated_at
            FROM container_state
            WHERE container_id = ?
            """,
            (container_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def upsert_container_state(
    container_id: str,
    container_name: str,
    last_restart_count: int,
    last_status: str,
) -> None:
    async def _op() -> None:
        async with _connect() as db:
            await db.execute(
                """
                INSERT INTO container_state (
                  container_id, container_name, last_restart_count, last_status, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(container_id) DO UPDATE SET
                  container_name=excluded.container_name,
                  last_restart_count=excluded.last_restart_count,
                  last_status=excluded.last_status,
                  updated_at=excluded.updated_at
                """,
                (container_id, container_name, last_restart_count, last_status, _utc_now_iso()),
            )
            await db.commit()

    await _execute_with_retry(_op)


async def insert_crash(record: dict) -> int:
    async def _op() -> int:
        async with _connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO crashes (
                  container_name, container_id, timestamp, exit_code, restart_count,
                   uptime_seconds, crash_type, ai_summary, raw_logs, acknowledged_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("container_name"),
                    record.get("container_id"),
                    record.get("timestamp"),
                    record.get("exit_code"),
                    record.get("restart_count"),
                    record.get("uptime_seconds"),
                    record.get("crash_type"),
                    record.get("ai_summary"),
                    record.get("raw_logs"),
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    return await _execute_with_retry(_op)


async def list_crashes(
    limit: int = 50,
    offset: int = 0,
    container: str | None = None,
    crash_type: str | None = None,
) -> list[dict]:
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    query = """
        SELECT id, container_name, container_id, timestamp, exit_code, restart_count,
               uptime_seconds, crash_type, ai_summary, acknowledged_at
        FROM crashes
    """
    clauses = []
    params: list = []

    if container:
        clauses.append("container_name LIKE ?")
        params.append(f"%{container}%")

    if crash_type:
        clauses.append("crash_type = ?")
        params.append(crash_type)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    query += " ORDER BY datetime(timestamp) DESC LIMIT ? OFFSET ?"
    params.append(safe_limit)
    params.append(safe_offset)

    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_crash(crash_id: int) -> dict | None:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, container_name, container_id, timestamp, exit_code, restart_count,
                     uptime_seconds, crash_type, ai_summary, raw_logs, acknowledged_at
            FROM crashes
            WHERE id = ?
            """,
            (crash_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_crash(crash_id: int) -> bool:
    async def _op() -> bool:
        async with _connect() as db:
            cursor = await db.execute("DELETE FROM crashes WHERE id = ?", (crash_id,))
            await db.commit()
            return cursor.rowcount > 0

    return await _execute_with_retry(_op)


async def delete_crashes(container: str | None = None, crash_type: str | None = None) -> int:
    clauses = []
    params: list = []

    if container:
        clauses.append("container_name LIKE ?")
        params.append(f"%{container}%")

    if crash_type:
        clauses.append("crash_type = ?")
        params.append(crash_type)

    query = "DELETE FROM crashes"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    async def _op() -> int:
        async with _connect() as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return int(cursor.rowcount or 0)

    return await _execute_with_retry(_op)


async def delete_old_crashes(older_than_days: int) -> int:
    if older_than_days <= 0:
        return 0

    query = "DELETE FROM crashes WHERE julianday(timestamp) < julianday('now', ?)"
    params = (f"-{older_than_days} days",)

    async def _op() -> int:
        async with _connect() as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return int(cursor.rowcount or 0)

    return await _execute_with_retry(_op)


async def get_crash_type_counts(limit: int = 10) -> list[dict]:
    safe_limit = max(1, min(int(limit), 50))
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT crash_type, COUNT(*) AS count
            FROM crashes
            GROUP BY crash_type
            ORDER BY count DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_timeline(hours: int = 24) -> list[dict]:
    safe_hours = max(1, min(int(hours), 168))
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT strftime('%Y-%m-%dT%H:00:00Z', timestamp) AS bucket, COUNT(*) AS count
            FROM crashes
            WHERE julianday(timestamp) >= julianday('now', ?)
            GROUP BY bucket
            ORDER BY bucket ASC
            """,
            (f"-{safe_hours} hours",),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_crashes_for_export(limit: int = 5000) -> list[dict]:
    safe_limit = max(1, min(int(limit), 20000))
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT id, container_name, container_id, timestamp, exit_code,
                     restart_count, uptime_seconds, crash_type, ai_summary, raw_logs, acknowledged_at
            FROM crashes
            ORDER BY datetime(timestamp) DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def set_container_mute(container_name: str, muted_until: str, reason: str | None = None) -> None:
    async def _op() -> None:
        async with _connect() as db:
            await db.execute(
                """
                INSERT INTO muted_containers (container_name, muted_until, reason, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(container_name) DO UPDATE SET
                  muted_until=excluded.muted_until,
                  reason=excluded.reason,
                  updated_at=excluded.updated_at
                """,
                (container_name, muted_until, reason, _utc_now_iso()),
            )
            await db.commit()

    await _execute_with_retry(_op)


async def clear_container_mute(container_name: str) -> bool:
    async def _op() -> bool:
        async with _connect() as db:
            cursor = await db.execute(
                "DELETE FROM muted_containers WHERE container_name = ?",
                (container_name,),
            )
            await db.commit()
            return bool(cursor.rowcount)

    return await _execute_with_retry(_op)


async def list_container_mutes() -> list[dict]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """
            SELECT container_name, muted_until, reason, updated_at
            FROM muted_containers
            WHERE julianday(muted_until) > julianday('now')
            ORDER BY muted_until ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def is_container_muted(container_name: str) -> bool:
    async with _connect() as db:
        cursor = await db.execute(
            """
            SELECT 1
            FROM muted_containers
            WHERE container_name = ?
              AND julianday(muted_until) > julianday('now')
            LIMIT 1
            """,
            (container_name,),
        )
        row = await cursor.fetchone()
        return bool(row)


async def acknowledge_crash(crash_id: int) -> bool:
    async def _op() -> bool:
        async with _connect() as db:
            cursor = await db.execute(
                "UPDATE crashes SET acknowledged_at = ? WHERE id = ?",
                (_utc_now_iso(), crash_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _execute_with_retry(_op)


async def unacknowledge_crash(crash_id: int) -> bool:
    async def _op() -> bool:
        async with _connect() as db:
            cursor = await db.execute(
                "UPDATE crashes SET acknowledged_at = NULL WHERE id = ?",
                (crash_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    return await _execute_with_retry(_op)


async def count_unacknowledged_crashes() -> int:
    async with _connect() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM crashes WHERE acknowledged_at IS NULL")
        row = await cursor.fetchone()
        return int(row[0] if row else 0)


async def get_stats() -> dict:
    today = datetime.now().date().isoformat()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row

        total_today_cur = await db.execute(
            "SELECT COUNT(*) AS count FROM crashes WHERE substr(timestamp, 1, 10) = ?",
            (today,),
        )
        total_today_row = await total_today_cur.fetchone()
        total_today = total_today_row["count"] if total_today_row else 0

        top_container_cur = await db.execute(
            """
            SELECT container_name, COUNT(*) AS count
            FROM crashes
            GROUP BY container_name
            ORDER BY count DESC
            LIMIT 1
            """
        )
        top_container_row = await top_container_cur.fetchone()

        top_type_cur = await db.execute(
            """
            SELECT crash_type, COUNT(*) AS count
            FROM crashes
            GROUP BY crash_type
            ORDER BY count DESC
            LIMIT 1
            """
        )
        top_type_row = await top_type_cur.fetchone()

        last_crash_cur = await db.execute(
            "SELECT timestamp FROM crashes ORDER BY datetime(timestamp) DESC LIMIT 1"
        )
        last_crash_row = await last_crash_cur.fetchone()

    return {
        "crashes_today": total_today,
        "most_affected_container": top_container_row["container_name"] if top_container_row else None,
        "most_common_crash_type": top_type_row["crash_type"] if top_type_row else None,
        "last_crash_time": last_crash_row["timestamp"] if last_crash_row else None,
    }
