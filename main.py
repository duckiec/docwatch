from __future__ import annotations

import asyncio
import csv
import io
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import docker
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

import database
import watcher
from notifier import send_notifications


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    watcher.start_watcher()
    yield
    watcher.stop_watcher()


app = FastAPI(title="DocWatch", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "").strip()


@app.middleware("http")
async def api_token_middleware(request: Request, call_next):
    if not API_AUTH_TOKEN:
        return await call_next(request)

    path = request.url.path
    if not path.startswith("/api") or path == "/api/health":
        return await call_next(request)

    token = request.headers.get("X-API-Token", "").strip()
    if token != API_AUTH_TOKEN:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/api/crashes")
async def api_crashes(
    container: str | None = Query(default=None),
    type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    acknowledged: bool | None = Query(default=None),
):
    crashes = await database.list_crashes(limit=limit, offset=offset, container=container, crash_type=type)
    if acknowledged is not None:
        crashes = [row for row in crashes if bool(row.get("acknowledged_at")) == acknowledged]
    return crashes


@app.get("/api/crashes/{crash_id}")
async def api_crash_detail(crash_id: int):
    crash = await database.get_crash(crash_id)
    if not crash:
        raise HTTPException(status_code=404, detail="Crash not found")
    return crash


@app.delete("/api/crashes/{crash_id}")
async def api_delete_crash(crash_id: int):
    deleted = await database.delete_crash(crash_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Crash not found")
    return {"ok": True}


@app.delete("/api/crashes")
async def api_delete_crashes(
    container: str | None = Query(default=None),
    type: str | None = Query(default=None),
):
    deleted = await database.delete_crashes(container=container, crash_type=type)
    return {"ok": True, "deleted": deleted}


@app.post("/api/crashes/{crash_id}/acknowledge")
async def api_acknowledge_crash(crash_id: int):
    updated = await database.acknowledge_crash(crash_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Crash not found")
    return {"ok": True}


@app.post("/api/crashes/{crash_id}/unacknowledge")
async def api_unacknowledge_crash(crash_id: int):
    updated = await database.unacknowledge_crash(crash_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Crash not found")
    return {"ok": True}


def _list_containers_sync() -> list[dict]:
    client = docker.from_env()
    try:
        data = []
        for c in client.containers.list(all=True):
            restart_count = int((c.attrs or {}).get("RestartCount", 0))
            data.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "status": c.status,
                    "restart_count": restart_count,
                }
            )
        return data
    finally:
        client.close()


@app.get("/api/containers")
async def api_containers():
    try:
        containers = await asyncio.to_thread(_list_containers_sync)
        return {"containers": containers, "error": watcher.get_last_error()}
    except Exception as exc:
        return {
            "containers": [],
            "error": f"Docker is not accessible. Verify docker socket mount. Details: {exc}",
        }


@app.get("/api/stats")
async def api_stats():
    stats = await database.get_stats()
    stats["docker_error"] = watcher.get_last_error()
    stats["unacknowledged_crashes"] = await database.count_unacknowledged_crashes()
    return stats


@app.get("/api/crash-types")
async def api_crash_types(limit: int = Query(default=10, ge=1, le=50)):
    return await database.get_crash_type_counts(limit=limit)


@app.get("/api/timeline")
async def api_timeline(hours: int = Query(default=24, ge=1, le=168)):
    return await database.get_timeline(hours=hours)


@app.post("/api/refresh")
async def api_refresh():
    return await watcher.trigger_poll_now()


@app.get("/api/export/crashes.csv")
async def api_export_crashes_csv(limit: int = Query(default=5000, ge=1, le=20000)):
    rows = await database.get_crashes_for_export(limit=limit)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "container_name",
            "container_id",
            "timestamp",
            "exit_code",
            "restart_count",
            "uptime_seconds",
            "crash_type",
            "ai_summary",
            "raw_logs",
            "acknowledged_at",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("id"),
                row.get("container_name"),
                row.get("container_id"),
                row.get("timestamp"),
                row.get("exit_code"),
                row.get("restart_count"),
                row.get("uptime_seconds"),
                row.get("crash_type"),
                row.get("ai_summary"),
                row.get("raw_logs"),
                row.get("acknowledged_at"),
            ]
        )

    csv_bytes = io.BytesIO(output.getvalue().encode("utf-8"))
    filename = f"docwatch-crashes-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.csv"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return StreamingResponse(csv_bytes, media_type="text/csv", headers=headers)


@app.get("/api/muted-containers")
async def api_list_muted_containers():
    return await database.list_container_mutes()


@app.post("/api/muted-containers")
async def api_set_muted_container(
    container_name: str = Query(..., min_length=1),
    minutes: int = Query(default=60, ge=5, le=10080),
    reason: str | None = Query(default=None),
):
    muted_until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    await database.set_container_mute(container_name=container_name, muted_until=muted_until, reason=reason)
    return {
        "ok": True,
        "container_name": container_name,
        "muted_until": muted_until,
        "reason": reason,
    }


@app.delete("/api/muted-containers")
async def api_clear_muted_container(container_name: str = Query(..., min_length=1)):
    removed = await database.clear_container_mute(container_name)
    if not removed:
        raise HTTPException(status_code=404, detail="Mute rule not found")
    return {"ok": True}


@app.post("/api/test-notify")
async def api_test_notify():
    try:
        result = await send_notifications(
            "DocWatch Test",
            "DocWatch test notification successful. If you received this, notifier config is working.",
        )
        return {"ok": True, "result": result}
    except Exception as exc:
        return {"ok": False, "result": {"telegram": False, "email": False}, "error": str(exc)}


@app.get("/api/health")
async def api_health():
    now = datetime.now(timezone.utc).isoformat()
    docker_error = watcher.get_last_error()
    return {
        "status": "degraded" if docker_error else "ok",
        "time": now,
        "watcher_running": watcher.scheduler.running,
        "docker_error": docker_error,
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
