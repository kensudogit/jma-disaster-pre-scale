"""EventBridge Scheduler から毎分起動される監視 Lambda。

  1. Atomフィードを条件付きGETで取得
  2. 新着電文だけを取得・検証・解析
  3. 判定
  4. 拡張/縮小が必要なら Step Functions を起動

多重起動は DynamoDB の分散ロックで抑止する。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ..models import Action
from ..notifier import audit_log, build_audit_entry
from ..orchestrator import Poller
from ..state import LockNotAcquired
from ._common import get_config, get_notifier, get_store

logger = logging.getLogger(__name__)


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    config = get_config()
    store = get_store()
    notifier = get_notifier()

    try:
        token = store.acquire_lock(
            config.safety.lock_ttl_seconds,
            owner=getattr(context, "aws_request_id", "poller"),
        )
    except LockNotAcquired:
        logger.info("ロック未取得のためスキップします")
        return {"statusCode": 200, "skipped": True, "reason": "lock_not_acquired"}

    try:
        outcome = Poller(config, store).run()
        decision = outcome.decision
        payload = decision.to_dict()

        audit_log(
            phase="poll",
            action=decision.action.value,
            level=payload["level_name"],
            reason=decision.reason,
            new_events=len(outcome.new_events),
            skipped_duplicates=outcome.skipped_duplicates,
            documents_fetched=outcome.documents_fetched,
            fetch_errors=outcome.fetch_errors,
            dry_run=config.dry_run,
        )
        store.record_audit(
            build_audit_entry(
                phase="poll",
                decision=payload,
                error="; ".join(outcome.fetch_errors),
                execution_id=getattr(context, "aws_request_id", ""),
            )
        )

        if outcome.fetch_errors:
            notifier.notify(
                f"[{config.service_name}] JMAフィード取得に一部失敗",
                {"errors": outcome.fetch_errors, "decision": payload},
            )

        started: str | None = None
        if decision.action in (Action.SCALE_OUT, Action.SCALE_IN):
            started = _start_execution(config, payload)

        return {
            "statusCode": 200,
            "decision": payload,
            "execution_arn": started,
            "skipped_duplicates": outcome.skipped_duplicates,
            "documents_fetched": outcome.documents_fetched,
            "fetch_errors": outcome.fetch_errors,
        }
    finally:
        store.release_lock(token)


def _start_execution(config: Any, payload: dict[str, Any]) -> str | None:
    arn = config.aws.state_machine_arn or os.environ.get("STATE_MACHINE_ARN", "")
    if not arn:
        logger.warning("state_machine_arn 未設定のため起動をスキップします")
        return None
    import boto3  # type: ignore

    sfn = boto3.client("stepfunctions", region_name=config.region)
    #  冪等な実行名(同じ判定を二重に流さない)
    name = f"{payload['level_name']}-{payload['decided_at']}".replace(":", "-")[:80]
    try:
        resp = sfn.start_execution(
            stateMachineArn=arn,
            name=name,
            input=json.dumps(payload, ensure_ascii=False),
        )
        return resp.get("executionArn")
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "ExecutionAlreadyExists":
            logger.info("同名の実行が既に存在します: %s", name)
            return None
        raise
