"""通知と監査ログ。SKILL.md 安全設計。

  - 操作前後の容量を記録する
  - CloudTrail と CloudWatch Logs へ証跡を残す(構造化JSONログ)
  - SNS へ成功・失敗を通知する
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Mapping

logger = logging.getLogger("jma_pre_scale.audit")

AUDIT_SCHEMA_VERSION = "1.0"


def audit_log(**fields: Any) -> dict[str, Any]:
    """CloudWatch Logs Insights で追える構造化ログを出す。"""
    entry = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "log_type": "jma_pre_scale_audit",
        **fields,
    }
    logger.info(json.dumps(entry, ensure_ascii=False, default=str, sort_keys=True))
    return entry


class Notifier:
    def __init__(self, sns_client: Any = None, topic_arn: str = "",
                 approval_topic_arn: str = "") -> None:
        self._sns = sns_client
        self._topic = topic_arn
        self._approval_topic = approval_topic_arn

    @classmethod
    def build(cls, config: Any) -> "Notifier":
        if not config.aws.notification_topic_arn:
            return cls()
        import boto3  # type: ignore

        return cls(
            boto3.client("sns", region_name=config.region),
            config.aws.notification_topic_arn,
            config.aws.approval_topic_arn or config.aws.notification_topic_arn,
        )

    def notify(self, subject: str, payload: Mapping[str, Any],
               *, approval: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        topic = self._approval_topic if approval else self._topic
        if not self._sns or not topic:
            logger.info("NOTIFY(local) %s\n%s", subject, body)
            return
        self._sns.publish(
            TopicArn=topic,
            Subject=subject[:100],
            Message=body,
        )


def build_audit_entry(
    *,
    phase: str,
    decision: Mapping[str, Any] | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    apply_result: Mapping[str, Any] | None = None,
    error: str = "",
    execution_id: str = "",
) -> dict[str, Any]:
    """DynamoDB 監査テーブルと CloudWatch Logs の双方に入れる共通形式。"""
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "phase": phase,
        "execution_id": execution_id or os.environ.get("_X_AMZN_TRACE_ID", ""),
        "decision": decision,
        "capacity_before": before,
        "capacity_after": after,
        "apply_result": apply_result,
        "error": error,
    }
