"""判定ルール。SKILL.md Phase 3 / 最重要制約 / 禁止事項の検証。"""
from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from conftest import fixture_bytes, make_config
from jma_pre_scale.models import Action, ScaleLevel, Severity, SystemState
from jma_pre_scale.parser import parse_report
from jma_pre_scale.rules import (
    clamp_target,
    decide,
    decide_on_feed_error,
    now_jst,
    target_for_level,
)
from jma_pre_scale.state import InMemoryStateStore, ScaleState


def events(name: str):
    return parse_report(fixture_bytes(name), source_url=f"https://example/{name}")


# ------------------------------------------------------------ 基本の拡張


def test_対象地域の警報でLEVEL_2へ拡張する(config, store):
    decision = decide(events("warning_tokyo_warning.xml"), config=config, state=store.get_state())
    assert decision.action is Action.SCALE_OUT
    assert decision.level is ScaleLevel.LEVEL_2
    assert decision.state is SystemState.WARNING
    assert decision.target.ecs_desired_count == 15


def test_特別警報はLEVEL_3かつ承認必須になる(config, store):
    decision = decide(events("warning_tokyo_emergency.xml"), config=config, state=store.get_state())
    assert decision.level is ScaleLevel.LEVEL_3
    assert decision.requires_approval is True
    # 承認が得られなくても1段下までは自動確保する
    assert decision.fallback_target.ecs_desired_count == 15


def test_震度6弱でLEVEL_3になる(config, store):
    decision = decide(events("earthquake_intensity.xml"), config=config, state=store.get_state())
    assert decision.action is Action.SCALE_OUT
    assert decision.level is ScaleLevel.LEVEL_3


def test_津波警報でLEVEL_3になる(config, store):
    decision = decide(events("tsunami_warning.xml"), config=config, state=store.get_state())
    assert decision.level is ScaleLevel.LEVEL_3


def test_災害種別ごとのレベル上書きが効く(store):
    config = make_config(severity_overrides={"earthquake": {"advisory": 2}})
    #  千葉県(震度4=advisory)だけを対象にする
    config = make_config(
        severity_overrides={"earthquake": {"advisory": 2}},
        jma={"target_area_codes": ["12"], "target_area_names": ["千葉県"]},
    )
    decision = decide(events("earthquake_intensity.xml"), config=config, state=store.get_state())
    assert decision.level is ScaleLevel.LEVEL_2  # 既定なら LEVEL_1


# -------------------------------------------------------- 除外・安全側


def test_対象外地域の特別警報では拡張しない(config, store):
    decision = decide(events("warning_osaka_emergency.xml"), config=config, state=store.get_state())
    assert decision.action is Action.NOOP
    assert decision.level is ScaleLevel.LEVEL_0


def test_訓練電文ではインフラを操作しない(config, store):
    decision = decide(events("warning_tokyo_drill.xml"), config=config, state=store.get_state())
    assert decision.action is Action.NOOP
    assert decision.level is ScaleLevel.LEVEL_0


def test_対象外の災害種別は無視する(store):
    config = make_config(jma={"supported_event_types": ["earthquake"]})
    decision = decide(events("warning_tokyo_warning.xml"), config=config, state=store.get_state())
    assert decision.action is Action.NOOP


def test_地域指定が空なら全国が対象になる(store):
    config = make_config(jma={"target_area_codes": [], "target_area_names": []})
    decision = decide(events("warning_osaka_emergency.xml"), config=config, state=store.get_state())
    assert decision.level is ScaleLevel.LEVEL_3


# ------------------------------------------------------- 縮小しない原則


def test_解除を受信しても即時縮小しない(config):
    state = ScaleState(current_level=ScaleLevel.LEVEL_3)
    decision = decide(events("warning_tokyo_release.xml"), config=config, state=state)
    assert decision.action is Action.HOLD
    assert decision.level is ScaleLevel.LEVEL_3
    assert decision.target.ecs_desired_count == 40


def test_取消を受信しても即時縮小しない(config):
    state = ScaleState(current_level=ScaleLevel.LEVEL_2)
    decision = decide(events("warning_tokyo_cancel.xml"), config=config, state=state)
    assert decision.action is Action.HOLD
    assert decision.level is ScaleLevel.LEVEL_2


def test_フィード取得失敗時は現在容量を維持する(config):
    state = ScaleState(current_level=ScaleLevel.LEVEL_2)
    decision = decide_on_feed_error("timeout", config=config, state=state)
    assert decision.action is Action.HOLD
    assert decision.level is ScaleLevel.LEVEL_2
    assert decision.state is SystemState.HOLD
    assert decision.target.ecs_desired_count == 15


def test_下位レベルの続報では容量を下げない(config):
    state = ScaleState(current_level=ScaleLevel.LEVEL_3)
    decision = decide(events("warning_tokyo_warning.xml"), config=config, state=state)
    assert decision.action is Action.NOOP
    assert decision.level is ScaleLevel.LEVEL_3


def test_訂正電文でも重大度が同じなら容量は変わらない(config):
    state = ScaleState(current_level=ScaleLevel.LEVEL_2)
    decision = decide(events("warning_tokyo_correction.xml"), config=config, state=state)
    assert decision.action is Action.NOOP
    assert decision.level is ScaleLevel.LEVEL_2


# ------------------------------------------------------------ 段階縮小


def test_自動縮小が無効なら常にHOLDのまま(config):
    state = ScaleState(
        current_level=ScaleLevel.LEVEL_3,
        cooldown_until=now_jst() - timedelta(hours=5),
    )
    decision = decide([], config=config, state=state)
    assert decision.action is Action.HOLD


def test_クールダウン中は縮小しない():
    config = make_config(safety={"allow_automatic_scale_in": True})
    state = ScaleState(
        current_level=ScaleLevel.LEVEL_3,
        cooldown_until=now_jst() + timedelta(minutes=30),
    )
    decision = decide([], config=config, state=state)
    assert decision.action is Action.HOLD
    assert decision.state is SystemState.COOLDOWN
    assert "残り" in decision.reason


def test_クールダウン未設定なら縮小せずクールダウンを開始する():
    config = make_config(safety={"allow_automatic_scale_in": True})
    state = ScaleState(current_level=ScaleLevel.LEVEL_2, cooldown_until=None)
    decision = decide([], config=config, state=state)
    assert decision.action is Action.HOLD
    assert "クールダウン" in decision.reason


def test_クールダウン満了で一段ずつ縮小する():
    config = make_config(safety={"allow_automatic_scale_in": True})
    state = ScaleState(
        current_level=ScaleLevel.LEVEL_3,
        cooldown_until=now_jst() - timedelta(minutes=1),
    )
    decision = decide([], config=config, state=state)
    assert decision.action is Action.SCALE_IN
    # 40 -> 35 (scale_in_step=5)。一気に平時容量へは戻さない。
    assert decision.target.ecs_desired_count == 35
    assert decision.level is ScaleLevel.LEVEL_3  # まだ LEVEL_2 目標に未到達


def test_縮小は平時容量を下回らない():
    config = make_config(safety={"allow_automatic_scale_in": True})
    state = ScaleState(
        current_level=ScaleLevel.LEVEL_1,
        cooldown_until=now_jst() - timedelta(minutes=1),
    )
    decision = decide([], config=config, state=state)
    assert decision.action is Action.SCALE_IN
    assert decision.target.ecs_desired_count == 2
    assert decision.level is ScaleLevel.LEVEL_0


def test_平時レベルでは何もしない(config, store):
    decision = decide([], config=config, state=store.get_state())
    assert decision.action is Action.NOOP
    assert decision.state is SystemState.NORMAL


# -------------------------------------------------- 手動オーバーライド


def test_自動制御停止が最優先される(config):
    state = ScaleState(current_level=ScaleLevel.LEVEL_1, automation_disabled=True)
    decision = decide(events("warning_tokyo_emergency.xml"), config=config, state=state)
    assert decision.action is Action.HOLD
    assert decision.level is ScaleLevel.LEVEL_1
    assert "automation_disabled" in decision.reason


def test_手動強制レベルが電文判定より優先される(config):
    state = ScaleState(current_level=ScaleLevel.LEVEL_0, forced_level=3)
    decision = decide([], config=config, state=state)
    assert decision.action is Action.SCALE_OUT
    assert decision.level is ScaleLevel.LEVEL_3
    assert decision.requires_approval is False  # 人が既に判断している


def test_手動強制レベルでの引き下げも可能(config):
    state = ScaleState(current_level=ScaleLevel.LEVEL_3, forced_level=1)
    decision = decide([], config=config, state=state)
    assert decision.action is Action.SCALE_IN
    assert decision.level is ScaleLevel.LEVEL_1


# -------------------------------------------------------------- クランプ


def test_絶対上限を超える容量はクランプされる():
    config = make_config(
        scaling={"level_3": {"ecs_desired_count": 999, "ecs_min_capacity": 999,
                             "aurora_min_acu": 999, "aurora_max_acu": 999},
                 "absolute_max_capacity": 50},
    )
    target = target_for_level(ScaleLevel.LEVEL_3, config)
    assert target.ecs_desired_count == 50
    assert target.ecs_min_capacity == 50
    assert target.aurora_min_acu == 64
    assert target.aurora_max_acu == 64


def test_常時予備容量を下回らない():
    config = make_config(
        scaling={"level_0": {"ecs_desired_count": 0, "ecs_min_capacity": 0,
                             "aurora_min_acu": 0.5, "aurora_max_acu": 8}},
        safety={"baseline_reserve_tasks": 3},
    )
    target = target_for_level(ScaleLevel.LEVEL_0, config)
    assert target.ecs_desired_count == 3, "地震等の突発災害でゼロ台起動に依存してはならない"


def test_MinCapacityはDesiredを超えない(config):
    from jma_pre_scale.models import ScalingTarget

    clamped = clamp_target(ScalingTarget(5, 99, 1.0, 8.0), config)
    assert clamped.ecs_min_capacity <= clamped.ecs_desired_count
