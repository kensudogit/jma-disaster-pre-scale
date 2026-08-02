from __future__ import annotations

from fastapi.testclient import TestClient

from jma_pre_scale.web import app


def test_health_and_status() -> None:
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "JMA" in page.text
    status = client.get("/api/v1/status")
    assert status.status_code == 200
    body = status.json()
    assert body["dry_run"] is True
    assert "feed_urls" in body
