"""フィード取得。SKILL.md Phase 2 / Phase 6「XML取得失敗」。

気象庁は 1日10GB を超えるダウンロードでIPを遮断するため、
条件付きGETが機能していることを必ず検証する。
"""
from __future__ import annotations

import urllib.error

import pytest

from conftest import fixture_bytes
from fakes import FakeResponse
from jma_pre_scale.feed import FeedError, fetch


def opener_returning(response, record: list | None = None):
    def _opener(request, timeout):
        if record is not None:
            record.append(request)
        return response

    return _opener


def test_正常取得できる():
    body = fixture_bytes("feed_extra.xml")
    result = fetch(
        "https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
        opener=opener_returning(
            FakeResponse(body, headers={"Content-Type": "application/xml",
                                        "ETag": '"abc"', "Last-Modified": "Sun, 02 Aug 2026 01:00:00 GMT"})
        ),
    )
    assert result.status == 200
    assert result.etag == '"abc"'
    assert result.body == body


def test_条件付きGETヘッダが送られる():
    requests: list = []
    fetch(
        "https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
        etag='"abc"',
        last_modified="Sun, 02 Aug 2026 01:00:00 GMT",
        opener=opener_returning(FakeResponse(b"<feed/>"), requests),
    )
    headers = {k.lower(): v for k, v in requests[0].headers.items()}
    assert headers["If-none-match".lower()] == '"abc"'
    assert headers["If-modified-since".lower()] == "Sun, 02 Aug 2026 01:00:00 GMT"


def test_304は本文なしで返る():
    def raise_304(_request, _timeout):
        raise urllib.error.HTTPError("u", 304, "Not Modified", {}, None)

    result = fetch("https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
                   etag='"abc"', opener=raise_304)
    assert result.not_modified is True
    assert result.body == b""


def test_https以外は拒否する():
    with pytest.raises(FeedError, match="https"):
        fetch("http://www.data.jma.go.jp/developer/xml/feed/extra.xml")


def test_許可外ホストは拒否する():
    with pytest.raises(FeedError, match="ホスト"):
        fetch("https://evil.example.com/feed.xml")


def test_想定外のContentTypeは拒否する():
    with pytest.raises(FeedError, match="Content-Type"):
        fetch(
            "https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
            opener=opener_returning(FakeResponse(b"<html/>", headers={"Content-Type": "text/html"})),
        )


def test_サイズ上限を超える応答は拒否する():
    with pytest.raises(FeedError, match="サイズ"):
        fetch(
            "https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
            max_bytes=5,
            opener=opener_returning(FakeResponse(b"x" * 100)),
        )


def test_5xxは再試行してから失敗する():
    attempts = []

    def flaky(_request, _timeout):
        attempts.append(1)
        raise urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)

    with pytest.raises(FeedError):
        fetch("https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
              opener=flaky, max_retries=2, sleep=lambda _s: None)
    assert len(attempts) == 3


def test_4xxは再試行しない():
    attempts = []

    def not_found(_request, _timeout):
        attempts.append(1)
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    with pytest.raises(FeedError):
        fetch("https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
              opener=not_found, max_retries=3, sleep=lambda _s: None)
    assert len(attempts) == 1, "4xx の再試行は帯域制限に抵触するため行わない"


def test_タイムアウトは再試行後に失敗する():
    attempts = []

    def timeout(_request, _timeout):
        attempts.append(1)
        raise TimeoutError("timed out")

    with pytest.raises(FeedError, match="取得に失敗"):
        fetch("https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
              opener=timeout, max_retries=1, sleep=lambda _s: None)
    assert len(attempts) == 2
