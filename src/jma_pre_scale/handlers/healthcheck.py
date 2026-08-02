"""Step Functions ヘルスチェックステップ。

起動途中は in_progress を返し、Step Functions 側で Wait -> 再試行する。
タイムアウトしても縮小はしない。通知だけ行う。
"""
from __future__ import annotations

from typing import Any

from ..models import ScalingTarget
from ..notifier import audit_log
from ._common import get_config, get_controller, get_notifier


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    config = get_config()
    controller = get_controller()
    target_raw = event["target"]
    target = ScalingTarget(
        ecs_desired_count=int(target_raw["ecs_desired_count"]),
        ecs_min_capacity=int(target_raw["ecs_min_capacity"]),
        aurora_min_acu=float(target_raw["aurora_min_acu"]),
        aurora_max_acu=float(target_raw["aurora_max_acu"]),
    )
    attempts = int(event.get("health_attempts", 0)) + 1
    result = controller.health_check(target)
    max_attempts = max(1, config.safety.health_check_timeout_seconds // 30)

    status = "HEALTHY"
    if not result["healthy"]:
        status = "UNHEALTHY"
    elif result["in_progress"]:
        status = "IN_PROGRESS" if attempts < max_attempts else "TIMEOUT"

    audit_log(phase="healthcheck", status=status, attempts=attempts, detail=result["details"])
    if status in ("UNHEALTHY", "TIMEOUT"):
        get_notifier().notify(
            f"[{config.service_name}] 事前スケール後のヘルスチェック異常({status})",
            {"detail": result["details"], "reason": event.get("reason", "")},
        )
    return {**event, "health_status": status, "health_detail": result["details"],
            "health_attempts": attempts}
