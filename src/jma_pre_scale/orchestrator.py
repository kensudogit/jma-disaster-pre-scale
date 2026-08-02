"""Poller の中核。フィード取得 -> 検証 -> 重複排除 -> 判定 を1本にまとめる。

Lambda ハンドラから薄く呼べるようにし、テストでは AWS なしで全経路を通せる。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Sequence

from .config import Config
from .feed import FeedError, FetchResult, fetch
from .models import Decision, DisasterEvent
from .parser import FeedEntry, XmlValidationError, parse_feed, parse_report
from .rules import decide, decide_on_feed_error
from .state import ScaleState, StateStore

logger = logging.getLogger(__name__)

Fetcher = Callable[..., FetchResult]


@dataclass
class PollOutcome:
    decision: Decision
    new_events: list[DisasterEvent]
    skipped_duplicates: int
    fetch_errors: list[str]
    documents_fetched: int


class Poller:
    def __init__(
        self,
        config: Config,
        store: StateStore,
        *,
        fetcher: Fetcher = fetch,
    ) -> None:
        self._config = config
        self._store = store
        self._fetch = fetcher

    def run(self, *, at: datetime | None = None) -> PollOutcome:
        cfg = self._config
        state: ScaleState = self._store.get_state()

        entries, fetch_errors = self._collect_entries()

        #  全フィードが失敗した場合のみ HOLD 扱いにする。
        #  一部でも取得できていれば、その範囲で判定する。
        if fetch_errors and not entries:
            return PollOutcome(
                decision=decide_on_feed_error(
                    "; ".join(fetch_errors), config=cfg, state=state, at=at
                ),
                new_events=[],
                skipped_duplicates=0,
                fetch_errors=fetch_errors,
                documents_fetched=0,
            )

        new_events: list[DisasterEvent] = []
        skipped = 0
        fetched = 0

        for entry in entries:
            if fetched >= cfg.jma.max_documents_per_run:
                logger.warning("max_documents_per_run に到達したため打ち切ります")
                break
            #  電文単位の重複排除(フィードは直近10分を常に含むため必須)
            if not self._store.mark_processed(entry.entry_id):
                skipped += 1
                continue
            try:
                result = self._fetch(
                    entry.link,
                    timeout=cfg.jma.request_timeout_seconds,
                    user_agent=cfg.jma.user_agent,
                    max_bytes=cfg.jma.max_document_bytes,
                    max_retries=cfg.jma.max_retries,
                    backoff_seconds=cfg.jma.retry_backoff_seconds,
                )
                fetched += 1
                events = parse_report(
                    result.body,
                    source_url=entry.link,
                    max_bytes=cfg.jma.max_document_bytes,
                )
            except (FeedError, XmlValidationError) as exc:
                #  1電文の失敗は全体を止めない。証跡だけ残す。
                fetch_errors.append(f"{entry.link}: {exc}")
                logger.warning("電文の取得/検証に失敗: %s: %s", entry.link, exc)
                continue
            new_events.extend(events)

        decision = decide(new_events, config=cfg, state=state, at=at)
        return PollOutcome(
            decision=decision,
            new_events=new_events,
            skipped_duplicates=skipped,
            fetch_errors=fetch_errors,
            documents_fetched=fetched,
        )

    # ------------------------------------------------------------------
    def _collect_entries(self) -> tuple[list[FeedEntry], list[str]]:
        cfg = self._config
        entries: list[FeedEntry] = []
        errors: list[str] = []
        seen_ids: set[str] = set()

        for url in cfg.jma.feed_urls:
            cache = self._store.get_feed_cache(url)
            try:
                result = self._fetch(
                    url,
                    timeout=cfg.jma.request_timeout_seconds,
                    user_agent=cfg.jma.user_agent,
                    etag=str(cache.get("etag") or ""),
                    last_modified=str(cache.get("last_modified") or ""),
                    max_bytes=cfg.jma.max_feed_bytes,
                    max_retries=cfg.jma.max_retries,
                    backoff_seconds=cfg.jma.retry_backoff_seconds,
                )
            except FeedError as exc:
                errors.append(f"{url}: {exc}")
                continue

            if result.not_modified:
                logger.info("feed not modified url=%s", url)
                continue
            try:
                parsed = parse_feed(result.body)
            except XmlValidationError as exc:
                errors.append(f"{url}: {exc}")
                continue

            self._store.put_feed_cache(url, result.etag, result.last_modified)
            for entry in parsed:
                if entry.entry_id in seen_ids:
                    continue
                seen_ids.add(entry.entry_id)
                entries.append(entry)

        return entries, errors
