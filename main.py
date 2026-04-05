from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import docker
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

import database
import watcher
from notifier import send_notifications

app = FastAPI(title="DocWatch")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
async def on_startup() -> None:
    await database.init_db()
    watcher.start_watcher()


@app.on_event("shutdown")
def on_shutdown() -> None:
    watcher.stop_watcher()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/crashes")
async def api_crashes(
    container: str | None = Query(default=None),
    type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    crashes = await database.list_crashes(limit=limit, container=container, crash_type=type)
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
    return stats


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
