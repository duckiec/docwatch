from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import database
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    with patch("watcher.start_watcher"), patch("watcher.stop_watcher"):
        with TestClient(main.app, raise_server_exceptions=True) as c:
            yield c


def test_api_token_auth_enforced_when_configured(client) -> None:
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
