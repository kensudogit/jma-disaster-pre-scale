"""フィード監視から適用まで一気通貫。SKILL.md Phase 6 の残りのケース。

AWS には一切接続せず、フェイクだけで全経路を通す。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import fixture_bytes, make_config
from fakes import FakeAutoScaling, FakeEcs, FakeRds, FakeResponse
from jma_pre_scale.controller import AwsClients, ScalingController
from jma_pre_scale.feed import FeedError, FetchResult
from jma_pre_scale.models import Action, ScaleLevel
from jma_pre_scale.orchestrator import Poller
from jma_pre_scale.rules import now_jst
from jma_pre_scale.state import InMemoryStateStore, ScaleState

FEED_URL = "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"


class ScriptedFetcher:
    """URL -> 返す内容 の対応表。未知のURLは FeedError にする。"""

    def __init__(self, mapping: dict[str, object]) -> None:
        self.mapping = mapping
        self.requested: list[str] = []

    def __call__(self, url: str, **kwargs) -> FetchResult:
        self.requested.append(url)
        value = self.mapping.get(url)
        if value is None:
            raise FeedError(f"not found: {url}")
        if isinstance(value, Exception):
            raise value
        if isinstance(value, FetchResult):
            return value
        return FetchResult(url=url, status=200, body=value, etag='"e1"',
                           last_modified="Sun, 02 Aug 2026 01:00:00 GMT")


def feed_with(*entry_urls: str) -> bytes:
    entries = "".join(
        f'<entry><title>t</title><id>{u}</id><updated>2026-08-02T01:00:00Z</updated>'
        f'<link type="application/xml" href="{u}"/></entry>'
        for u in entry_urls
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>f</title><id>f</id>"
        f"{entries}</feed>"
    ).encode("utf-8")


DOC_WARNING = "https://www.data.jma.go.jp/developer/xml/data/w1.xml"
DOC_EMERGENCY = "https://www.data.jma.go.jp/developer/xml/data/e1.xml"
DOC_RELEASE = "https://www.data.jma.go.jp/developer/xml/data/r1.xml"


def build_poller(mapping, config=None, state=None):
    config = config or make_config()
    store = InMemoryStateStore("test", state)
    fetcher = ScriptedFetcher(mapping)
    return Poller(config, store, fetcher=fetcher), store, fetcher


# ---------------------------------------------------------- 正常系


def test_フィードから電文を取得して拡張判定に至る():
    poller, _, _ = build_poller({
        FEED_URL: feed_with(DOC_WARNING),
        DOC_WARNING: fixture_bytes("warning_tokyo_warning.xml"),
    })
    outcome = poller.run()
    assert outcome.decision.action is Action.SCALE_OUT
    assert outcome.decision.level is ScaleLevel.LEVEL_2
    assert outcome.documents_fetched == 1


def test_2回目の実行では同じ電文を再取得しない():
    poller, store, fetcher = build_poller({
        FEED_URL: feed_with(DOC_WARNING),
        DOC_WARNING: fixture_bytes("warning_tokyo_warning.xml"),
    })
    poller.run()
    before = len(fetcher.requested)

    second = poller.run()
    assert second.skipped_duplicates == 1
    assert second.documents_fetched == 0
    assert len([u for u in fetcher.requested[before:] if u == DOC_WARNING]) == 0


def test_フィードが304なら電文を取りに行かない():
    from jma_pre_scale.feed import FetchResult as FR

    poller, store, fetcher = build_poller({
        FEED_URL: FR(url=FEED_URL, status=304, body=b""),
        DOC_WARNING: fixture_bytes("warning_tokyo_warning.xml"),
    })
    outcome = poller.run()
    assert outcome.documents_fetched == 0
    assert outcome.decision.action is Action.NOOP
    assert DOC_WARNING not in fetcher.requested


def test_複数電文のうち最も重大なものでレベルが決まる():
    poller, _, _ = build_poller({
        FEED_URL: feed_with(DOC_WARNING, DOC_EMERGENCY),
        DOC_WARNING: fixture_bytes("warning_tokyo_warning.xml"),
        DOC_EMERGENCY: fixture_bytes("warning_tokyo_emergency.xml"),
    })
    outcome = poller.run()
    assert outcome.decision.level is ScaleLevel.LEVEL_3


# ---------------------------------------------------------- 異常系


def test_全フィードの取得に失敗したらHOLDになる():
    poller, _, _ = build_poller(
        {FEED_URL: FeedError("timeout")},
        state=ScaleState(current_level=ScaleLevel.LEVEL_2),
    )
    outcome = poller.run()
    assert outcome.decision.action is Action.HOLD
    assert outcome.decision.level is ScaleLevel.LEVEL_2
    assert outcome.fetch_errors


def test_一部の電文取得に失敗しても他は処理される():
    poller, _, _ = build_poller({
        FEED_URL: feed_with(DOC_WARNING, DOC_EMERGENCY),
        DOC_EMERGENCY: fixture_bytes("warning_tokyo_emergency.xml"),
        # DOC_WARNING は未登録 -> FeedError
    })
    outcome = poller.run()
    assert outcome.decision.level is ScaleLevel.LEVEL_3
    assert len(outcome.fetch_errors) == 1


def test_壊れた電文は破棄され判定に影響しない():
    poller, _, _ = build_poller({
        FEED_URL: feed_with(DOC_WARNING),
        DOC_WARNING: fixture_bytes("malformed.xml"),
    })
    outcome = poller.run()
    assert outcome.decision.action is Action.NOOP
    assert outcome.fetch_errors


def test_一度に処理する電文数に上限がある():
    urls = [f"https://www.data.jma.go.jp/developer/xml/data/d{i}.xml" for i in range(10)]
    mapping = {FEED_URL: feed_with(*urls)}
    for u in urls:
        mapping[u] = fixture_bytes("warning_tokyo_warning.xml")
    config = make_config(jma={"max_documents_per_run": 3})
    poller, _, _ = build_poller(mapping, config=config)
    outcome = poller.run()
    assert outcome.documents_fetched == 3


# ------------------------------------------------- 判定から適用まで


def apply_decision(decision, *, ecs=None, aas=None, rds=None, dry_run=False):
    config = make_config(dry_run=dry_run)
    clients = AwsClients(
        ecs=ecs or FakeEcs(),
        application_autoscaling=aas or FakeAutoScaling(),
        rds=rds or FakeRds(),
    )
    controller = ScalingController(config, clients)
    return controller.apply(decision.target, scale_in=(decision.action is Action.SCALE_IN))


def test_警報受信から実際の容量拡張まで通る():
    poller, _, _ = build_poller({
        FEED_URL: feed_with(DOC_WARNING),
        DOC_WARNING: fixture_bytes("warning_tokyo_warning.xml"),
    })
    decision = poller.run().decision
    ecs, aas, rds = FakeEcs(), FakeAutoScaling(), FakeRds()
    result = apply_decision(decision, ecs=ecs, aas=aas, rds=rds)

    assert result.status == "SUCCEEDED"
    assert ecs.desired == 15
    assert aas.min_capacity == 15
    assert rds.min_acu == 8.0


def test_解除受信では容量が下がらない():
    poller, _, _ = build_poller(
        {FEED_URL: feed_with(DOC_RELEASE), DOC_RELEASE: fixture_bytes("warning_tokyo_release.xml")},
        state=ScaleState(current_level=ScaleLevel.LEVEL_3),
    )
    decision = poller.run().decision
    assert decision.action is Action.HOLD

    ecs = FakeEcs(desired=40)
    aas = FakeAutoScaling(min_capacity=40)
    result = apply_decision(decision, ecs=ecs, aas=aas, rds=FakeRds(min_acu=16))
    assert ecs.desired == 40, "解除直後に平時容量へ戻してはならない"


def test_取得失敗時に適用しても容量は維持される():
    poller, _, _ = build_poller(
        {FEED_URL: FeedError("timeout")},
        state=ScaleState(current_level=ScaleLevel.LEVEL_3),
    )
    decision = poller.run().decision
    ecs = FakeEcs(desired=40)
    apply_decision(decision, ecs=ecs, aas=FakeAutoScaling(min_capacity=40),
                   rds=FakeRds(min_acu=16))
    assert ecs.desired == 40


def test_DryRunでは電文を受けても実容量が変わらない():
    poller, _, _ = build_poller({
        FEED_URL: feed_with(DOC_EMERGENCY),
        DOC_EMERGENCY: fixture_bytes("warning_tokyo_emergency.xml"),
    })
    decision = poller.run().decision
    assert decision.dry_run is True

    ecs = FakeEcs()
    result = apply_decision(decision, ecs=ecs, dry_run=True)
    assert result.status == "DRY_RUN"
    assert ecs.desired == 2


def test_自動制御停止中は電文を受けても拡張しない():
    poller, _, _ = build_poller(
        {FEED_URL: feed_with(DOC_EMERGENCY),
         DOC_EMERGENCY: fixture_bytes("warning_tokyo_emergency.xml")},
        state=ScaleState(current_level=ScaleLevel.LEVEL_0, automation_disabled=True),
    )
    decision = poller.run().decision
    assert decision.action is Action.HOLD
    assert decision.level is ScaleLevel.LEVEL_0


def test_手動強制拡張は電文なしでも適用される():
    poller, _, _ = build_poller(
        {FEED_URL: feed_with()},
        state=ScaleState(current_level=ScaleLevel.LEVEL_0, forced_level=3),
    )
    decision = poller.run().decision
    ecs = FakeEcs()
    result = apply_decision(decision, ecs=ecs)
    assert result.status == "SUCCEEDED"
    assert ecs.desired == 40


def test_段階縮小が実容量へ反映される():
    config = make_config(safety={"allow_automatic_scale_in": True})
    poller, _, _ = build_poller(
        {FEED_URL: feed_with()},
        config=config,
        state=ScaleState(current_level=ScaleLevel.LEVEL_3,
                         cooldown_until=now_jst() - timedelta(minutes=1)),
    )
    decision = poller.run().decision
    assert decision.action is Action.SCALE_IN

    ecs = FakeEcs(desired=40)
    aas = FakeAutoScaling(min_capacity=40)
    rds = FakeRds(min_acu=16.0, max_acu=64.0)
    result = apply_decision(decision, ecs=ecs, aas=aas, rds=rds)
    assert ecs.desired == 35
    assert rds.max_acu == 64.0, "MaxACUは維持する"
