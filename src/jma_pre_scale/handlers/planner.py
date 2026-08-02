"""Step Functions 最初のステップ: 現況取得と適用計画(Dry Run出力)の作成。

本番APIを呼ぶ前に、必ず「今の容量」と「これから適用する容量」を突き合わせる。
"""
from __future__ import annotations

from typing import Any

from ..models import ScalingTarget
from ..notifier import audit_log
from ._common import get_config, get_controller


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    config = get_config()
    controller = get_controller()
    target_raw = event.get("target") or {}
    target = ScalingTarget(
        ecs_desired_count=int(target_raw["ecs_desired_count"]),
        ecs_min_capacity=int(target_raw["ecs_min_capacity"]),
        aurora_min_acu=float(target_raw["aurora_min_acu"]),
        aurora_max_acu=float(target_raw["aurora_max_acu"]),
    )
    before = controller.describe_current()
    plan = {
        **event,
        "capacity_before": before,
        "plan": {
            "ecs": {
                "from": (before.get("ecs") or {}).get("desiredCount"),
                "to": target.ecs_desired_count,
            },
            "ecs_min_capacity": {
                "from": (before.get("application_autoscaling") or {}).get("MinCapacity"),
                "to": target.ecs_min_capacity,
            },
            "aurora_min_acu": {
                "from": ((before.get("aurora") or {})
                         .get("ServerlessV2ScalingConfiguration") or {}).get("MinCapacity"),
                "to": target.aurora_min_acu,
            },
        },
        "dry_run": config.dry_run,
        "requires_approval": bool(event.get("requires_approval")) and not config.dry_run,
    }
    audit_log(phase="plan", plan=plan["plan"], dry_run=config.dry_run,
              reason=event.get("reason", ""))
    return plan
