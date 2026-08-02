"""状態管理・重複排除・分散ロック。SKILL.md Phase 6「XML重複受信」。"""
from __future__ import annotations

import dataclasses

import pytest

from jma_pre_scale.models import ScaleLevel, SystemState
from jma_pre_scale.state import InMemoryStateStore, LockNotAcquired, ScaleState


def test_同じ電文IDは一度しか処理されない(store):
    assert store.mark_processed("evt-1") is True
    assert store.mark_processed("evt-1") is False
    assert store.mark_processed("evt-2") is True


def test_ロックは同時に一つしか取れない(store):
    token = store.acquire_lock(60, owner="a")
    with pytest.raises(LockNotAcquired):
        store.acquire_lock(60, owner="b")
    store.release_lock(token)
    assert store.acquire_lock(60, owner="b")


def test_他人のロックは解放できない(store):
    token = store.acquire_lock(60, owner="a")
    store.release_lock("someone-else")
    with pytest.raises(LockNotAcquired):
        store.acquire_lock(60, owner="b")
    store.release_lock(token)


def test_楽観ロックで競合更新を防ぐ(store):
    state = store.get_state()
    store.put_state(dataclasses.replace(state, current_level=ScaleLevel.LEVEL_2))
    with pytest.raises(RuntimeError, match="ConditionalCheckFailed"):
        store.put_state(dataclasses.replace(state, current_level=ScaleLevel.LEVEL_3))


def test_状態はitem往復で保存される():
    state = ScaleState(
        current_level=ScaleLevel.LEVEL_2,
        system_state=SystemState.WARNING,
        forced_level=3,
        automation_disabled=True,
        last_reason="test",
    )
    restored = ScaleState.from_item(state.to_item())
    assert restored.current_level is ScaleLevel.LEVEL_2
    assert restored.system_state is SystemState.WARNING
    assert restored.forced_level == 3
    assert restored.automation_disabled is True


def test_監査ログが記録される(store):
    store.record_audit({"phase": "apply", "status": "SUCCEEDED"})
    assert store.audits == [{"phase": "apply", "status": "SUCCEEDED"}]


def test_フィードキャッシュを保持できる(store):
    assert store.get_feed_cache("https://x") == {}
    store.put_feed_cache("https://x", '"e1"', "Sun, 02 Aug 2026 01:00:00 GMT")
    assert store.get_feed_cache("https://x")["etag"] == '"e1"'
