from __future__ import annotations

from fastapi.testclient import TestClient

from jma_pre_scale.web import app


def test_health_and_status() -> None:
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200
    assert "JMA" in page.text
    assert 'id="usage-guide"' in page.text
    assert "利用手順" in page.text
    assert "ARCHITECTURE" in page.text
    assert "詳細利用手順" in page.text
    assert "ドラッグで移動" in page.text
    assert "Service topology" in page.text
    assert "LEVEL_3" in page.text
    status = client.get("/api/v1/status")
    assert status.status_code == 200
    body = status.json()
    assert body["dry_run"] is True
    assert "feed_urls" in body
