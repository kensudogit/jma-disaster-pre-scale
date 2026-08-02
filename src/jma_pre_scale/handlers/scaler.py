"""Step Functions 実適用ステップ。ECS Fargate と Aurora Serverless v2 を拡張する。"""
from __future__ import annotations

from typing import Any

from ..models import Action, ScaleLevel, ScalingTarget, SystemState
from ..notifier import audit_log, build_audit_entry
from ..state import ScaleState
from ._common import get_config, get_controller, get_notifier, get_store


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    config = get_config()
    controller = get_controller()
    store = get_store()
    notifier = get_notifier()

    target_raw = event["target"]
    target = ScalingTarget(
        ecs_desired_count=int(target_raw["ecs_desired_count"]),
        ecs_min_capacity=int(target_raw["ecs_min_capacity"]),
        aurora_min_acu=float(target_raw["aurora_min_acu"]),
        aurora_max_acu=float(target_raw["aurora_max_acu"]),
    )
    action = Action(event.get("action", "SCALE_OUT"))
    before = event.get("capacity_before") or controller.describe_current()

    result = controller.apply(target, scale_in=(action is Action.SCALE_IN))
    after = controller.describe_current() if not config.dry_run else target.to_dict()

    # 適用に完全成功した場合のみ状態を進める。
    # 失敗・部分成功では現在レベルを維持し、次回判定が同じ拡張を再試行できるようにする。
    if result.status in ("SUCCEEDED", "DRY_RUN"):
        _persist_state(store, config, event, target)

    payload = {
        **event,
        "apply_result": result.to_dict(),
        "capacity_before": before,
        "capacity_after": after,
    }
    audit_log(phase="apply", status=result.status, action=action.value,
              target=target.to_dict(), dry_run=config.dry_run)
    store.record_audit(
        build_audit_entry(
            phase="apply",
            decision={k: event.get(k) for k in ("action", "level_name", "reason")},
            before=before,
            after=after if isinstance(after, dict) else None,
            apply_result=result.to_dict(),
            execution_id=getattr(context, "aws_request_id", ""),
        )
    )
    if result.status in ("PARTIAL", "FAILED"):
        notifier.notify(
            f"[{config.service_name}] 事前スケール適用に失敗({result.status})",
            payload,
        )
    return payload


def _persist_state(store: Any, config: Any, event: dict[str, Any],
                   target: ScalingTarget) -> None:
    from datetime import timedelta

    from ..rules import now_jst

    current = store.get_state()
    level = ScaleLevel(int(event["level"]))
    action = Action(event.get("action", "SCALE_OUT"))
    cooldown = None
    if action is Action.SCALE_OUT and level > ScaleLevel.LEVEL_0:
        cooldown = None  # 拡張時はクールダウンを張らない(解除受信時に張る)
    elif action is Action.SCALE_IN and level > ScaleLevel.LEVEL_0:
        cooldown = now_jst() + timedelta(minutes=config.safety.cooldown_minutes)

    store.put_state(
        ScaleState(
            current_level=level,
            system_state=SystemState(event.get("state", SystemState.for_level(level).value)),
            cooldown_until=cooldown,
            forced_level=current.forced_level,
            automation_disabled=current.automation_disabled,
            version=current.version,
            last_reason=str(event.get("reason", "")),
            applied_target=target.to_dict(),
        )
    )
