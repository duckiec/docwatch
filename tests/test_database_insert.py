from __future__ import annotations

import asyncio
from pathlib import Path

import database


def test_insert_crash_persists_row(tmp_path: Path) -> None:
    db_file = tmp_path / "docwatch-test.db"
    original_db_path = database.DB_PATH

    try:
        database.DB_PATH = str(db_file)
        asyncio.run(database.init_db())

        crash_id = asyncio.run(
            database.insert_crash(
                {
                    "container_name": "svc-a",
                    "container_id": "abc123",
                    "timestamp": "2026-04-05T00:00:00+00:00",
                    "exit_code": 1,
                    "restart_count": 2,
                    "uptime_seconds": 15,
                    "crash_type": "Config error",
                    "ai_summary": "Config load failed.",
                    "raw_logs": "Traceback...",
                }
            )
        )

        assert crash_id > 0
        row = asyncio.run(database.get_crash(crash_id))
        assert row is not None
        assert row["container_name"] == "svc-a"
    finally:
        database.DB_PATH = original_db_path
