from __future__ import annotations

import asyncio

from classifier import classify_crash
from main import app, api_health
from summarizer import get_summarizer


def test_health_shape() -> None:
    payload = asyncio.run(api_health())
    assert set(payload) >= {"status", "time", "watcher_running", "docker_error"}


def test_routes_include_health_and_refresh() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/health" in paths
    assert "/api/refresh" in paths
    assert "/api/crashes" in paths


def test_classifier_rules() -> None:
    assert classify_crash(137, "OOMKilled", 120) == "OOM"
    assert classify_crash(1, "config missing", 12) == "Config error"
    assert classify_crash(0, "exited normally", 60) == "Clean exit"
    assert classify_crash(1, "connection refused to backend", 500) == "Network"


def test_summarizer_factory_returns_object() -> None:
    assert get_summarizer() is not None
