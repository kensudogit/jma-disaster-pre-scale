"""判定ルール。SKILL.md Phase 3。

    災害種別 + 対象地域 + 情報レベル + 現在容量 + クールダウン状態

安全側の原則(SKILL.md 最重要制約 / 禁止事項)をここに集約する。

  - XML取得失敗時は縮小せず、現在容量を維持する          -> decide_on_feed_error()
  - 解除情報を受信しても即時縮小せず、クールダウンを置く   -> _decide_release()
  - 地震等の突発災害では常時予備容量を持つ                -> clamp_target()
  - 手動オーバーライドを最優先する                        -> decide() 冒頭
  - 訓練・試験電文はインフラ操作へ接続しない              -> filter_events()
  - 最大容量に上限を設ける                                -> clamp_target()
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping, Sequence

from .config import Config
from .models import (
    Action,
    Decision,
    DisasterEvent,
    ScaleLevel,
    ScalingTarget,
    Severity,
    SystemState,
)
from .state import ScaleState

JST = timezone(timedelta(hours=9))

DEFAULT_SEVERITY_MAP: Mapping[Severity, ScaleLevel] = {
    Severity.NONE: ScaleLevel.LEVEL_0,
    Severity.ADVISORY: ScaleLevel.LEVEL_1,
    Severity.WARNING: ScaleLevel.LEVEL_2,
    Severity.EMERGENCY_WARNING: ScaleLevel.LEVEL_3,
}


def now_jst() -> datetime:
    return datetime.now(tz=JST)


# ------------------------------------------------------------------ 絞り込み


def is_target_area(event: DisasterEvent, config: Config) -> bool:
    """対象地域か判定する。

    コード体系は 府県予報区(6桁) / 地震の府県(2桁) / 津波予報区(3桁) で異なるため、
    コード完全一致に加えて地域名でも一致を取る。両方未設定なら全国を対象とする。
    """
    codes = config.jma.target_area_codes
    names = config.jma.target_area_names
    if not codes and not names:
        return True
    if event.area_code and event.area_code in codes:
        return True
    if event.area_name and event.area_name in names:
        return True
    return False


def filter_events(
    events: Iterable[DisasterEvent], config: Config
) -> tuple[list[DisasterEvent], list[DisasterEvent]]:
    """(判定対象, 除外) に振り分ける。"""
    kept: list[DisasterEvent] = []
    dropped: list[DisasterEvent] = []
    supported = set(config.jma.supported_event_types)
    for event in events:
        if event.is_drill and not config.jma.accept_drill_messages:
            dropped.append(event)
            continue
        if event.event_type not in supported:
            dropped.append(event)
            continue
        if not is_target_area(event, config):
            dropped.append(event)
            continue
        kept.append(event)
    return kept, dropped


# -------------------------------------------------------------- レベル決定


def severity_to_level(event: DisasterEvent, config: Config) -> ScaleLevel:
    """重大度をスケールレベルへ写像する。災害種別ごとの上書きを尊重する。"""
    override = config.severity_overrides.get(event.event_type)
    if override and event.severity.value in override:
        return ScaleLevel(int(override[event.severity.value]))
    return DEFAULT_SEVERITY_MAP[event.severity]


def requested_level(events: Sequence[DisasterEvent], config: Config) -> ScaleLevel:
    """有効(未解除)な電文群から要求レベルを求める。最大値を採る。"""
    level = ScaleLevel.LEVEL_0
    for event in events:
        if event.is_cancelled:
            continue
        level = max(level, severity_to_level(event, config))
    return level


def clamp_target(target: ScalingTarget, config: Config) -> ScalingTarget:
    """上限クランプと常時予備容量の下限を適用する。"""
    max_tasks = config.safety.absolute_max_ecs_tasks
    max_acu = config.safety.absolute_max_aurora_acu
    floor = config.safety.baseline_reserve_tasks
    if max_tasks < 1:
        raise ValueError("absolute_max_ecs_tasks must be positive")
    if floor > max_tasks:
        raise ValueError("baseline_reserve_tasks が absolute_max_ecs_tasks を超えています")

    desired = min(max(target.ecs_desired_count, floor), max_tasks)
    min_cap = min(max(target.ecs_min_capacity, floor), max_tasks)
    min_cap = min(min_cap, desired)  # MinCapacity は Desired を超えない
    min_acu = min(max(target.aurora_min_acu, 0.5), max_acu)
    max_acu_v = min(max(target.aurora_max_acu, min_acu), max_acu)
    return ScalingTarget(
        ecs_desired_count=desired,
        ecs_min_capacity=min_cap,
        aurora_min_acu=min_acu,
        aurora_max_acu=max_acu_v,
    )


def target_for_level(level: ScaleLevel, config: Config) -> ScalingTarget:
    return clamp_target(config.target_for(level), config)


# ------------------------------------------------------------------ 判定本体


def decide(
    events: Sequence[DisasterEvent],
    *,
    config: Config,
    state: ScaleState,
    at: datetime | None = None,
) -> Decision:
    """新着電文と現在状態からアクションを決定する。"""
    at = at or now_jst()
    stamp = at.isoformat()

    # 1) 手動オーバーライドを最優先する
    if state.automation_disabled:
        return Decision(
            action=Action.HOLD,
            level=state.current_level,
            previous_level=state.current_level,
            state=SystemState.HOLD,
            reason="手動により自動制御が停止されています(automation_disabled=true)",
            target=target_for_level(state.current_level, config),
            events=[e.to_dict() for e in events],
            dry_run=config.dry_run,
            decided_at=stamp,
        )
    if state.forced_level is not None:
        forced = ScaleLevel(state.forced_level)
        return Decision(
            action=Action.SCALE_OUT if forced >= state.current_level else Action.SCALE_IN,
            level=forced,
            previous_level=state.current_level,
            state=SystemState.for_level(forced),
            reason=f"手動強制レベル {forced.name} が設定されています",
            target=target_for_level(forced, config),
            events=[e.to_dict() for e in events],
            requires_approval=False,  # 人が既に判断している
            dry_run=config.dry_run,
            decided_at=stamp,
        )

    kept, dropped = filter_events(events, config)
    current = state.current_level

    # 2) 対象電文なし: 何もしない(現在容量は維持)
    if not kept:
        reason = "対象となる電文がありません"
        if dropped:
            reason += f"(対象外 {len(dropped)} 件を除外)"
        return _hold_or_cooldown(config, state, at, reason, events, cooldown_allowed=True)

    requested = requested_level(kept, config)
    active = [e for e in kept if not e.is_cancelled]

    # 3) 解除・取消のみ: 即時縮小せずクールダウンへ
    if not active:
        return _hold_or_cooldown(
            config, state, at,
            "解除・取消電文のみを受信しました。即時縮小は行いません",
            events, cooldown_allowed=True,
        )

    # 4) 有効な電文あり: 現在レベルを下回る拡張はしない(単調増加)
    new_level = max(current, requested)
    if new_level == current:
        # 同レベルの続報。クールダウンだけ解除して現状維持。
        return Decision(
            action=Action.NOOP,
            level=current,
            previous_level=current,
            state=SystemState.for_level(current),
            reason=f"要求レベル {requested.name} は現在レベル {current.name} 以下のため変更なし",
            target=target_for_level(current, config),
            events=[e.to_dict() for e in events],
            dry_run=config.dry_run,
            decided_at=stamp,
        )

    requires_approval = (
        new_level is ScaleLevel.LEVEL_3
        and config.safety.require_manual_approval_for_level_3
    )
    #  承認が得られない場合でも「何もしない」は避け、1段下の容量までは自動で確保する。
    fallback = (
        target_for_level(ScaleLevel(int(new_level) - 1), config)
        if requires_approval and new_level > ScaleLevel.LEVEL_0
        else None
    )
    trigger = max(active, key=lambda e: severity_to_level(e, config))
    return Decision(
        action=Action.SCALE_OUT,
        level=new_level,
        previous_level=current,
        state=SystemState.for_level(new_level),
        reason=(
            f"{trigger.title}({trigger.event_type}/{trigger.severity.value}"
            f"/area={trigger.area_code or trigger.area_name})により "
            f"{current.name} -> {new_level.name}"
        ),
        target=target_for_level(new_level, config),
        events=[e.to_dict() for e in events],
        requires_approval=requires_approval,
        fallback_target=fallback,
        dry_run=config.dry_run,
        decided_at=stamp,
    )


def _hold_or_cooldown(
    config: Config,
    state: ScaleState,
    at: datetime,
    reason: str,
    events: Sequence[DisasterEvent],
    *,
    cooldown_allowed: bool,
) -> Decision:
    """平時 or クールダウン中の判断。段階縮小はここだけで起こる。"""
    stamp = at.isoformat()
    current = state.current_level
    payload = [e.to_dict() for e in events]

    if current is ScaleLevel.LEVEL_0:
        return Decision(
            action=Action.NOOP,
            level=ScaleLevel.LEVEL_0,
            previous_level=ScaleLevel.LEVEL_0,
            state=SystemState.NORMAL,
            reason=reason + " / 平時容量のため変更なし",
            target=target_for_level(ScaleLevel.LEVEL_0, config),
            events=payload,
            dry_run=config.dry_run,
            decided_at=stamp,
        )

    if not cooldown_allowed or not config.safety.allow_automatic_scale_in:
        return Decision(
            action=Action.HOLD,
            level=current,
            previous_level=current,
            state=SystemState.COOLDOWN if state.cooldown_until else SystemState.HOLD,
            reason=reason + " / 自動縮小は無効のため現在容量を維持します",
            target=target_for_level(current, config),
            events=payload,
            dry_run=config.dry_run,
            decided_at=stamp,
        )

    cooldown_until = state.cooldown_until
    if cooldown_until is None:
        return Decision(
            action=Action.HOLD,
            level=current,
            previous_level=current,
            state=SystemState.COOLDOWN,
            reason=(
                reason
                + f" / クールダウン {config.safety.cooldown_minutes} 分を開始します"
            ),
            target=target_for_level(current, config),
            events=payload,
            dry_run=config.dry_run,
            decided_at=stamp,
        )

    if at < cooldown_until:
        remaining = int((cooldown_until - at).total_seconds() // 60)
        return Decision(
            action=Action.HOLD,
            level=current,
            previous_level=current,
            state=SystemState.COOLDOWN,
            reason=reason + f" / クールダウン中(残り約{remaining}分)",
            target=target_for_level(current, config),
            events=payload,
            dry_run=config.dry_run,
            decided_at=stamp,
        )

    #  クールダウン満了。1段だけ下げる(一気に平時容量へ戻さない)
    next_level = ScaleLevel(max(int(ScaleLevel.LEVEL_0), int(current) - 1))
    base = target_for_level(next_level, config)
    current_target = target_for_level(current, config)
    step = max(1, config.safety.scale_in_step)
    stepped_desired = max(base.ecs_desired_count,
                          current_target.ecs_desired_count - step)
    target = clamp_target(
        ScalingTarget(
            ecs_desired_count=stepped_desired,
            ecs_min_capacity=min(base.ecs_min_capacity, stepped_desired),
            aurora_min_acu=base.aurora_min_acu,
            aurora_max_acu=max(base.aurora_max_acu, current_target.aurora_max_acu),
        ),
        config,
    )
    reached = target.ecs_desired_count <= base.ecs_desired_count
    return Decision(
        action=Action.SCALE_IN,
        level=next_level if reached else current,
        previous_level=current,
        state=SystemState.COOLDOWN if next_level is not ScaleLevel.LEVEL_0 else SystemState.NORMAL,
        reason=reason + f" / クールダウン満了により1段縮小({step}タスク刻み)",
        target=target,
        events=payload,
        dry_run=config.dry_run,
        decided_at=stamp,
    )


def decide_on_feed_error(
    error: str, *, config: Config, state: ScaleState, at: datetime | None = None
) -> Decision:
    """XML取得・検証失敗時。絶対に縮小せず現在容量を維持する。"""
    at = at or now_jst()
    return Decision(
        action=Action.HOLD,
        level=state.current_level,
        previous_level=state.current_level,
        state=SystemState.HOLD,
        reason=f"フィード取得・検証に失敗したため現在容量を維持します: {error}",
        target=target_for_level(state.current_level, config),
        events=(),
        dry_run=config.dry_run,
        decided_at=at.isoformat(),
    )
