"""気象庁Atomフィード/電文の取得。SKILL.md Phase 2。

重要:
  気象庁は「1日10GB以上のダウンロードを伴うアクセス」を検知するとIPを遮断する。
  そのため ETag / Last-Modified による条件付きGETを必ず行い、
  変化がなければ本文を取得しない。取得済み電文は DynamoDB で重複排除する。

標準ライブラリのみで実装しているため、Lambda に外部依存を持ち込まない。
"""
from __future__ import annotations

import gzip
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

_ALLOWED_HOSTS = ("www.data.jma.go.jp", "xml.kishou.go.jp")
_ALLOWED_CONTENT_TYPES = ("application/xml", "text/xml", "application/atom+xml")


class FeedError(RuntimeError):
    """取得失敗。呼び出し側は必ず HOLD に倒すこと(縮小してはならない)。"""


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    body: bytes
    etag: str = ""
    last_modified: str = ""

    @property
    def not_modified(self) -> bool:
        return self.status == 304


Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310


def _validate_url(url: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise FeedError(f"httpsのみ許可されています: {url}")
    if parsed.hostname not in _ALLOWED_HOSTS:
        raise FeedError(f"許可されていないホストです: {parsed.hostname}")


def fetch(
    url: str,
    *,
    timeout: float = 10.0,
    user_agent: str = "jma-disaster-pre-scale/1.0",
    etag: str = "",
    last_modified: str = "",
    max_bytes: int = 8 * 1024 * 1024,
    max_retries: int = 2,
    backoff_seconds: float = 1.0,
    opener: Opener | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchResult:
    """条件付きGETでURLを取得する。304 は body 空で返す。"""
    _validate_url(url)
    opener = opener or _default_opener

    headers = {
        "User-Agent": user_agent,
        "Accept": "application/atom+xml, application/xml, text/xml",
        "Accept-Encoding": "gzip",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with opener(request, timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                resp_headers = _headers_of(response)
                content_type = (resp_headers.get("Content-Type") or "").split(";")[0].strip()
                if content_type and not any(
                    content_type == allowed for allowed in _ALLOWED_CONTENT_TYPES
                ):
                    raise FeedError(f"想定外のContent-Typeです: {content_type} ({url})")

                declared = resp_headers.get("Content-Length")
                if declared and int(declared) > max_bytes:
                    raise FeedError(f"サイズ上限超過: {declared} > {max_bytes} ({url})")

                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise FeedError(f"サイズ上限超過: >{max_bytes} bytes ({url})")
                if (resp_headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
                    if len(body) > max_bytes:
                        raise FeedError(f"展開後サイズ上限超過: >{max_bytes} bytes ({url})")

                return FetchResult(
                    url=url,
                    status=int(status),
                    body=body,
                    etag=resp_headers.get("ETag", "") or "",
                    last_modified=resp_headers.get("Last-Modified", "") or "",
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return FetchResult(url=url, status=304, body=b"", etag=etag,
                                   last_modified=last_modified)
            last_error = exc
            if exc.code < 500 and exc.code != 429:
                break  # 4xx は再試行しない
        except FeedError:
            raise
        except Exception as exc:  # URLError, socket.timeout など
            last_error = exc

        if attempt < max_retries:
            wait = backoff_seconds * (2 ** attempt)
            logger.warning("fetch retry url=%s attempt=%s wait=%.1fs err=%s",
                           url, attempt + 1, wait, last_error)
            sleep(wait)

    raise FeedError(f"取得に失敗しました: {url}: {last_error}")


def _headers_of(response: Any) -> Mapping[str, str]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    try:
        return {k: v for k, v in headers.items()}
    except Exception:  # pragma: no cover
        return dict(headers)
