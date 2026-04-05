from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import docker
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import classifier
import database
from notifier import send_crash_notification
from summarizer import get_summarizer


def _get_int_env(name: str, default: int, minimum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return max(minimum, value)
    except Exception:
        return default


MAX_LOG_LINES = _get_int_env("MAX_LOG_LINES", 200, 10)
POLL_INTERVAL_SECONDS = _get_int_env("POLL_INTERVAL_SECONDS", 30, 5)
CRASH_RETENTION_DAYS = _get_int_env("CRASH_RETENTION_DAYS", 30, 0)
RETENTION_SWEEP_MINUTES = _get_int_env("RETENTION_SWEEP_MINUTES", 60, 5)

scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 20})
_summarizer = get_summarizer()
_last_error: str | None = None
logger = logging.getLogger("docwatch.watcher")
_last_retention_sweep: datetime | None = None


def get_last_error() -> str | None:
    return _last_error


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return default


def _parse_uptime_seconds(attrs: dict) -> int | None:
    state = attrs.get("State", {})
    started_at = state.get("StartedAt")
    finished_at = state.get("FinishedAt")
    if not started_at:
        return None

    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if finished_at and finished_at != "0001-01-01T00:00:00Z":
            finished = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
        else:
            finished = datetime.now(timezone.utc)
        return max(0, int((finished - started).total_seconds()))
    except Exception:
        return None


def _truncate_logs(logs: str, max_lines: int) -> str:
    if not logs:
        return ""
    lines = logs.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def _collect_containers_sync() -> list:
    global _last_error
    client = docker.from_env()
    try:
        containers = client.containers.list(all=True)
        _last_error = None
        return containers
    except Exception as exc:
        _last_error = f"Docker unavailable: {exc}"
        return []
    finally:
        try:
            client.close()
        except Exception:
            pass


def _collect_crash_details_sync(container, max_log_lines: int) -> dict:
    attrs = container.attrs or {}
    state = attrs.get("State", {})

    logs_raw = container.logs(tail=max_log_lines)
    logs_text = logs_raw.decode("utf-8", errors="replace") if isinstance(logs_raw, (bytes, bytearray)) else str(logs_raw)
    logs_text = _truncate_logs(logs_text, max_log_lines)

    memory_usage = None
    memory_limit = None
    try:
        stats = container.stats(stream=False)
        memory_usage = (stats.get("memory_stats") or {}).get("usage")
        memory_limit = (stats.get("memory_stats") or {}).get("limit")
    except Exception:
        pass

    return {
        "exit_code": state.get("ExitCode"),
        "restart_count": _safe_int(attrs.get("RestartCount", 0)),
        "uptime_seconds": _parse_uptime_seconds(attrs),
        "logs": logs_text,
        "memory_usage": memory_usage,
        "memory_limit": memory_limit,
    }


async def poll_docker() -> None:
    try:
        containers = await asyncio.to_thread(_collect_containers_sync)
    except Exception as exc:
        logger.exception("Failed to poll Docker")
        global _last_error
        _last_error = f"Docker poll failed: {exc}"
        return

    await _maybe_run_retention_cleanup()

    if not containers:
        return

    for container in containers:
        try:
            attrs = container.attrs or {}
            container_id = container.id
            container_name = container.name
            status = container.status
            restart_count = _safe_int(attrs.get("RestartCount", 0))

            previous = await database.get_container_state(container_id)
            if previous is None:
                await database.upsert_container_state(container_id, container_name, restart_count, status)
                continue

            restart_increased = restart_count > _safe_int(previous.get("last_restart_count", 0))
            newly_exited = status == "exited" and previous.get("last_status") != "exited"

            if restart_increased or newly_exited:
                details = await asyncio.to_thread(_collect_crash_details_sync, container, MAX_LOG_LINES)
                crash_type = classifier.classify_crash(
                    details.get("exit_code"),
                    details.get("logs", ""),
                    details.get("uptime_seconds"),
                )

                payload = {
                    "container_name": container_name,
                    "container_id": container_id,
                    "exit_code": details.get("exit_code"),
                    "restart_count": details.get("restart_count"),
                    "memory_usage": details.get("memory_usage"),
                    "memory_limit": details.get("memory_limit"),
                    "logs": details.get("logs", ""),
                    "crash_type": crash_type,
                }
                ai_summary = await _summarizer.summarize(payload)

                crash_record = {
                    "container_name": container_name,
                    "container_id": container_id,
                    "timestamp": _utc_now_iso(),
                    "exit_code": details.get("exit_code"),
                    "restart_count": details.get("restart_count"),
                    "uptime_seconds": details.get("uptime_seconds"),
                    "crash_type": crash_type,
                    "ai_summary": ai_summary,
                    "raw_logs": details.get("logs", ""),
                }

                await database.insert_crash(crash_record)
                muted = await database.is_container_muted(container_name)
                if not muted:
                    await send_crash_notification(crash_record)

            await database.upsert_container_state(container_id, container_name, restart_count, status)
        except Exception:
            logger.exception("Container processing failed")


async def trigger_poll_now() -> dict:
    await poll_docker()
    return {
        "ok": True,
        "docker_error": _last_error,
        "polled_at": _utc_now_iso(),
    }


async def _maybe_run_retention_cleanup() -> None:
    global _last_retention_sweep
    if CRASH_RETENTION_DAYS <= 0:
        return

    now = datetime.now(timezone.utc)
    if _last_retention_sweep is not None:
        delta = now - _last_retention_sweep
        if delta < timedelta(minutes=RETENTION_SWEEP_MINUTES):
            return

    _last_retention_sweep = now
    try:
        deleted = await database.delete_old_crashes(CRASH_RETENTION_DAYS)
        if deleted:
            logger.info("Retention sweep removed %s old crash rows", deleted)
    except Exception:
        logger.exception("Retention cleanup failed")


def start_watcher() -> None:
    if not scheduler.running:
        scheduler.add_job(poll_docker, "interval", seconds=POLL_INTERVAL_SECONDS, id="docwatch-poller", replace_existing=True)
        scheduler.start()


def stop_watcher() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
