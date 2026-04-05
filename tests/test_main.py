"""Tests for main.py FastAPI endpoints."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# We need to prevent watcher from starting the scheduler during import
with patch("watcher.scheduler") as _mock_sched:
    _mock_sched.running = False
    import main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with startup/shutdown lifespan suppressed."""
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    # Prevent scheduler side-effects in tests
    with patch("watcher.start_watcher"), patch("watcher.stop_watcher"):
        with TestClient(main.app, raise_server_exceptions=True) as c:
            yield c


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_home_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# GET /api/crashes
# ---------------------------------------------------------------------------

def test_api_crashes_empty(client):
    with patch("database.list_crashes", new_callable=AsyncMock, return_value=[]):
        resp = client.get("/api/crashes")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_crashes_returns_data(client):
    crashes = [{"id": 1, "container_name": "web", "crash_type": "OOM"}]
    with patch("database.list_crashes", new_callable=AsyncMock, return_value=crashes):
        resp = client.get("/api/crashes")
    assert resp.status_code == 200
    assert resp.json() == crashes


def test_api_crashes_filters_forwarded(client):
    with patch("database.list_crashes", new_callable=AsyncMock, return_value=[]) as mock_list:
        resp = client.get("/api/crashes?container=web&type=OOM&limit=10&offset=5")
    assert resp.status_code == 200
    mock_list.assert_awaited_once_with(limit=10, offset=5, container="web", crash_type="OOM")


def test_api_crashes_invalid_limit(client):
    resp = client.get("/api/crashes?limit=0")
    assert resp.status_code == 422


def test_api_crashes_limit_too_large(client):
    resp = client.get("/api/crashes?limit=999")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/crashes/{crash_id}
# ---------------------------------------------------------------------------

def test_api_crash_detail_found(client):
    crash = {"id": 42, "container_name": "db", "crash_type": "Exit 1"}
    with patch("database.get_crash", new_callable=AsyncMock, return_value=crash):
        resp = client.get("/api/crashes/42")
    assert resp.status_code == 200
    assert resp.json()["id"] == 42


def test_api_crash_detail_not_found(client):
    with patch("database.get_crash", new_callable=AsyncMock, return_value=None):
        resp = client.get("/api/crashes/9999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# DELETE /api/crashes/{crash_id}
# ---------------------------------------------------------------------------

def test_api_delete_crash_success(client):
    with patch("database.delete_crash", new_callable=AsyncMock, return_value=True):
        resp = client.delete("/api/crashes/1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_api_delete_crash_not_found(client):
    with patch("database.delete_crash", new_callable=AsyncMock, return_value=False):
        resp = client.delete("/api/crashes/9999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/crashes
# ---------------------------------------------------------------------------

def test_api_delete_crashes_all(client):
    with patch("database.delete_crashes", new_callable=AsyncMock, return_value=5):
        resp = client.delete("/api/crashes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted"] == 5


def test_api_delete_crashes_with_filters(client):
    with patch("database.delete_crashes", new_callable=AsyncMock, return_value=2) as mock_del:
        resp = client.delete("/api/crashes?container=web&type=OOM")
    assert resp.status_code == 200
    mock_del.assert_awaited_once_with(container="web", crash_type="OOM")


# ---------------------------------------------------------------------------
# GET /api/containers
# ---------------------------------------------------------------------------

def test_api_containers_success(client):
    containers = [{"id": "abc", "name": "web", "status": "running", "restart_count": 0}]
    with patch("main._list_containers_sync", return_value=containers):
        resp = client.get("/api/containers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["containers"] == containers


def test_api_containers_docker_unavailable(client):
    with patch("main._list_containers_sync", side_effect=Exception("docker not found")):
        resp = client.get("/api/containers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["containers"] == []
    assert "docker" in data["error"].lower()


# ---------------------------------------------------------------------------
# GET /api/stats
# ---------------------------------------------------------------------------

def test_api_stats(client):
    stats = {
        "crashes_today": 3,
        "most_affected_container": "web",
        "most_common_crash_type": "OOM",
        "last_crash_time": "2024-01-01T00:00:00+00:00",
    }
    with patch("database.get_stats", new_callable=AsyncMock, return_value=stats):
        with patch("watcher.get_last_error", return_value=None):
            resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["crashes_today"] == 3
    assert data["docker_error"] is None


# ---------------------------------------------------------------------------
# GET /api/crash-types
# ---------------------------------------------------------------------------

def test_api_crash_types(client):
    types = [{"crash_type": "OOM", "count": 5}]
    with patch("database.get_crash_type_counts", new_callable=AsyncMock, return_value=types):
        resp = client.get("/api/crash-types")
    assert resp.status_code == 200
    assert resp.json() == types


def test_api_crash_types_limit_param(client):
    with patch("database.get_crash_type_counts", new_callable=AsyncMock, return_value=[]) as mock:
        resp = client.get("/api/crash-types?limit=5")
    assert resp.status_code == 200
    mock.assert_awaited_once_with(limit=5)


# ---------------------------------------------------------------------------
# GET /api/timeline
# ---------------------------------------------------------------------------

def test_api_timeline(client):
    timeline = [{"bucket": "2024-01-01T00:00:00Z", "count": 2}]
    with patch("database.get_timeline", new_callable=AsyncMock, return_value=timeline):
        resp = client.get("/api/timeline?hours=12")
    assert resp.status_code == 200
    assert resp.json() == timeline


# ---------------------------------------------------------------------------
# POST /api/refresh
# ---------------------------------------------------------------------------

def test_api_refresh(client):
    result = {"ok": True, "docker_error": None, "polled_at": "2024-01-01T00:00:00+00:00"}
    with patch("watcher.trigger_poll_now", new_callable=AsyncMock, return_value=result):
        resp = client.post("/api/refresh")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# GET /api/export/crashes.csv
# ---------------------------------------------------------------------------

def test_api_export_csv(client):
    rows = [
        {
            "id": 1, "container_name": "web", "container_id": "abc",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "exit_code": 1, "restart_count": 2, "uptime_seconds": 30,
            "crash_type": "Exit 1", "ai_summary": "error", "raw_logs": "traceback",
        }
    ]
    with patch("database.get_crashes_for_export", new_callable=AsyncMock, return_value=rows):
        resp = client.get("/api/export/crashes.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    content = resp.content.decode("utf-8")
    assert "container_name" in content  # header
    assert "web" in content             # data row


def test_api_export_csv_empty(client):
    with patch("database.get_crashes_for_export", new_callable=AsyncMock, return_value=[]):
        resp = client.get("/api/export/crashes.csv")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    # Header row only
    assert "id" in content
    lines = [l for l in content.splitlines() if l.strip()]
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# GET /api/muted-containers
# ---------------------------------------------------------------------------

def test_api_list_muted_containers(client):
    mutes = [{"container_name": "web", "muted_until": "2099-01-01T00:00:00+00:00", "reason": "maint"}]
    with patch("database.list_container_mutes", new_callable=AsyncMock, return_value=mutes):
        resp = client.get("/api/muted-containers")
    assert resp.status_code == 200
    assert resp.json() == mutes


# ---------------------------------------------------------------------------
# POST /api/muted-containers
# ---------------------------------------------------------------------------

def test_api_set_muted_container(client):
    with patch("database.set_container_mute", new_callable=AsyncMock):
        resp = client.post("/api/muted-containers?container_name=web&minutes=30&reason=deploy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["container_name"] == "web"
    assert data["reason"] == "deploy"


def test_api_set_muted_container_empty_name(client):
    resp = client.post("/api/muted-containers?container_name=")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/muted-containers
# ---------------------------------------------------------------------------

def test_api_clear_muted_container_success(client):
    with patch("database.clear_container_mute", new_callable=AsyncMock, return_value=True):
        resp = client.delete("/api/muted-containers?container_name=web")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_api_clear_muted_container_not_found(client):
    with patch("database.clear_container_mute", new_callable=AsyncMock, return_value=False):
        resp = client.delete("/api/muted-containers?container_name=nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/test-notify
# ---------------------------------------------------------------------------

def test_api_test_notify_success(client):
    result = {"telegram": True, "email": False}
    with patch("main.send_notifications", new_callable=AsyncMock, return_value=result):
        resp = client.post("/api/test-notify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["result"]["telegram"] is True


def test_api_test_notify_exception(client):
    with patch("main.send_notifications", new_callable=AsyncMock, side_effect=RuntimeError("smtp down")):
        resp = client.post("/api/test-notify")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "smtp down" in data["error"]


def test_api_token_middleware_enforced(client):
    original_token = main.API_AUTH_TOKEN
    try:
        main.API_AUTH_TOKEN = "secret-token"

        unauthorized = client.get("/api/stats")
        assert unauthorized.status_code == 401

        authorized = client.get("/api/stats", headers={"X-API-Token": "secret-token"})
        assert authorized.status_code == 200

        health = client.get("/api/health")
        assert health.status_code == 200
    finally:
        main.API_AUTH_TOKEN = original_token


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

def test_api_health_ok(client):
    mock_sched = MagicMock()
    mock_sched.running = True
    with patch("watcher.get_last_error", return_value=None), \
         patch.object(main.watcher, "scheduler", mock_sched):
        resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["watcher_running"] is True
    assert data["docker_error"] is None


def test_api_health_degraded(client):
    mock_sched = MagicMock()
    mock_sched.running = True
    with patch("watcher.get_last_error", return_value="Docker unavailable"), \
         patch.object(main.watcher, "scheduler", mock_sched):
        resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["docker_error"] == "Docker unavailable"
