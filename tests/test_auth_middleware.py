from __future__ import annotations

from fastapi.testclient import TestClient

import main


def test_api_token_auth_enforced_when_configured() -> None:
    client = TestClient(main.app)
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
