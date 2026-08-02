"""Step Functions 終端ステップ。成功・失敗を SNS へ通知する。"""
from __future__ import annotations

from typing import Any

from ..notifier import audit_log
from ._common import get_config, get_notifier


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    config = get_config()
    status = event.get("health_status") or (
        event.get("apply_result") or {}
    ).get("status", "UNKNOWN")
    subject = f"[{config.service_name}] 事前スケール {event.get('action', '')} {status}"
    body = {
        "action": event.get("action"),
        "level": event.get("level_name"),
        "reason": event.get("reason"),
        "dry_run": event.get("dry_run"),
        "target": event.get("target"),
        "capacity_before": event.get("capacity_before"),
        "capacity_after": event.get("capacity_after"),
        "apply_result": event.get("apply_result"),
        "health_detail": event.get("health_detail"),
        "error": event.get("error"),
    }
    get_notifier().notify(subject, body)
    audit_log(phase="notify", status=status, action=event.get("action"))
    return {**event, "notified": True}
