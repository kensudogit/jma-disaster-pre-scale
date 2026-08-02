"""状態管理・重複排除・分散ロック・監査証跡(DynamoDB)。

SKILL.md 安全設計:
  - 同一イベントの多重実行を防ぐ            -> mark_processed() の条件付き書き込み
  - DynamoDB等で分散ロックを行う            -> acquire_lock() / release_lock()
  - 操作前後の容量を記録する                -> record_audit()

単一テーブル設計:

  pk                      | sk               | 用途
  ------------------------|------------------|------------------------------
  STATE#<service>         | CURRENT          | 現在レベル/クールダウン/手動制御
  EVENT#<service>         | <event_id_hash>  | 重複排除(TTL付き)
  LOCK#<service>          | CURRENT          | 分散ロック(TTL付き)
  AUDIT#<service>#<YYYYMM>| <iso8601>#<uuid> | 監査証跡(TTL付き)
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Protocol

from .models import ScaleLevel, ScalingTarget, SystemState

JST = timezone(timedelta(hours=9))
EVENT_TTL_SECONDS = 7 * 24 * 3600
AUDIT_TTL_SECONDS = 400 * 24 * 3600


class LockNotAcquired(RuntimeError):
    """他の実行がロックを保持している。安全に処理を打ち切る。"""


@dataclass(frozen=True)
class ScaleState:
    """現在の制御状態。"""

    current_level: ScaleLevel = ScaleLevel.LEVEL_0
    system_state: SystemState = SystemState.NORMAL
    cooldown_until: datetime | None = None
    forced_level: int | None = None
    automation_disabled: bool = False
    version: int = 0
    updated_at: str = ""
    last_reason: str = ""
    applied_target: Mapping[str, Any] | None = None

    def to_item(self) -> dict[str, Any]:
        return {
            "current_level": int(self.current_level),
            "system_state": self.system_state.value,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "forced_level": self.forced_level,
            "automation_disabled": self.automation_disabled,
            "version": self.version,
            "updated_at": self.updated_at,
            "last_reason": self.last_reason,
            "applied_target": _decimalize(self.applied_target) if self.applied_target else None,
        }

    @classmethod
    def from_item(cls, item: Mapping[str, Any] | None) -> "ScaleState":
        if not item:
            return cls()
        cooldown_raw = item.get("cooldown_until")
        cooldown = datetime.fromisoformat(cooldown_raw) if cooldown_raw else None
        forced = item.get("forced_level")
        return cls(
            current_level=ScaleLevel(int(item.get("current_level", 0))),
            system_state=SystemState(item.get("system_state", "NORMAL")),
            cooldown_until=cooldown,
            forced_level=int(forced) if forced is not None else None,
            automation_disabled=bool(item.get("automation_disabled", False)),
            version=int(item.get("version", 0)),
            updated_at=str(item.get("updated_at", "")),
            last_reason=str(item.get("last_reason", "")),
            applied_target=item.get("applied_target"),
        )


def _decimalize(value: Any) -> Any:
    """DynamoDB は float を受け付けないため Decimal に落とす。"""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {k: _decimalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decimalize(v) for v in value]
    return value


def event_key(event_id: str) -> str:
    return hashlib.sha256(event_id.encode("utf-8")).hexdigest()


class StateStore(Protocol):
    """Poller/Controller が依存する最小インターフェース。"""

    def get_state(self) -> ScaleState: ...
    def put_state(self, state: ScaleState) -> ScaleState: ...
    def mark_processed(self, event_id: str) -> bool: ...
    def acquire_lock(self, ttl_seconds: int, owner: str) -> str: ...
    def release_lock(self, token: str) -> None: ...
    def record_audit(self, entry: Mapping[str, Any]) -> None: ...
    def get_feed_cache(self, url: str) -> Mapping[str, Any]: ...
    def put_feed_cache(self, url: str, etag: str, last_modified: str) -> None: ...


# --------------------------------------------------------------- DynamoDB


class DynamoStateStore:
    """DynamoDB 実装。boto3 の resource("dynamodb").Table を受け取る。"""

    def __init__(self, table: Any, service_name: str) -> None:
        self._table = table
        self._service = service_name

    # 状態 -------------------------------------------------------------
    def get_state(self) -> ScaleState:
        resp = self._table.get_item(
            Key={"pk": f"STATE#{self._service}", "sk": "CURRENT"},
            ConsistentRead=True,
        )
        return ScaleState.from_item(resp.get("Item"))

    def put_state(self, state: ScaleState) -> ScaleState:
        """楽観ロック付き更新。競合したら例外を投げて呼び出し側で再試行する。"""
        new = replace(
            state,
            version=state.version + 1,
            updated_at=datetime.now(tz=JST).isoformat(),
        )
        item = {"pk": f"STATE#{self._service}", "sk": "CURRENT", **new.to_item()}
        self._table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(pk) OR version = :expected",
            ExpressionAttributeValues={":expected": state.version},
        )
        return new

    # 重複排除 ---------------------------------------------------------
    def mark_processed(self, event_id: str) -> bool:
        """初回なら True、既処理なら False。条件付き書き込みで原子的に判定する。"""
        try:
            self._table.put_item(
                Item={
                    "pk": f"EVENT#{self._service}",
                    "sk": event_key(event_id),
                    "event_id": event_id,
                    "processed_at": datetime.now(tz=JST).isoformat(),
                    "ttl": int(time.time()) + EVENT_TTL_SECONDS,
                },
                ConditionExpression="attribute_not_exists(sk)",
            )
            return True
        except Exception as exc:  # botocore ConditionalCheckFailedException
            if _is_conditional_failure(exc):
                return False
            raise

    # ロック -----------------------------------------------------------
    def acquire_lock(self, ttl_seconds: int, owner: str = "") -> str:
        token = f"{owner or 'lambda'}:{uuid.uuid4()}"
        now = int(time.time())
        try:
            self._table.put_item(
                Item={
                    "pk": f"LOCK#{self._service}",
                    "sk": "CURRENT",
                    "token": token,
                    "expires_at": now + ttl_seconds,
                    "ttl": now + ttl_seconds,
                },
                ConditionExpression=(
                    "attribute_not_exists(pk) OR expires_at < :now"
                ),
                ExpressionAttributeValues={":now": now},
            )
            return token
        except Exception as exc:
            if _is_conditional_failure(exc):
                raise LockNotAcquired("他の実行がロックを保持しています") from exc
            raise

    def release_lock(self, token: str) -> None:
        try:
            self._table.delete_item(
                Key={"pk": f"LOCK#{self._service}", "sk": "CURRENT"},
                ConditionExpression="#t = :token",
                ExpressionAttributeNames={"#t": "token"},
                ExpressionAttributeValues={":token": token},
            )
        except Exception as exc:
            if not _is_conditional_failure(exc):
                raise  # 自分のロックでなければ何もしない

    # 監査 -------------------------------------------------------------
    def record_audit(self, entry: Mapping[str, Any]) -> None:
        now = datetime.now(tz=JST)
        self._table.put_item(
            Item={
                "pk": f"AUDIT#{self._service}#{now:%Y%m}",
                "sk": f"{now.isoformat()}#{uuid.uuid4()}",
                "ttl": int(time.time()) + AUDIT_TTL_SECONDS,
                **_decimalize(dict(entry)),
            }
        )


    # 条件付きGETキャッシュ ------------------------------------------
    def get_feed_cache(self, url: str) -> Mapping[str, Any]:
        resp = self._table.get_item(
            Key={"pk": f"FEED#{self._service}", "sk": event_key(url)}
        )
        return resp.get("Item") or {}

    def put_feed_cache(self, url: str, etag: str, last_modified: str) -> None:
        self._table.put_item(
            Item={
                "pk": f"FEED#{self._service}",
                "sk": event_key(url),
                "url": url,
                "etag": etag,
                "last_modified": last_modified,
                "ttl": int(time.time()) + EVENT_TTL_SECONDS,
            }
        )


def _is_conditional_failure(exc: Exception) -> bool:
    name = type(exc).__name__
    if name == "ConditionalCheckFailedException":
        return True
    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
    return code == "ConditionalCheckFailedException"


# ------------------------------------------------------------- In-memory


class InMemoryStateStore:
    """テストと Dry Run 用。AWS 接続なしで全ロジックを検証できる。"""

    def __init__(self, service_name: str = "test", state: ScaleState | None = None) -> None:
        self._service = service_name
        self._state = state or ScaleState()
        self.processed: set[str] = set()
        self.audits: list[dict[str, Any]] = []
        self._lock: tuple[str, float] | None = None
        self._feed_cache: dict[str, dict[str, Any]] = {}

    def get_state(self) -> ScaleState:
        return self._state

    def put_state(self, state: ScaleState) -> ScaleState:
        if state.version != self._state.version:
            raise RuntimeError("ConditionalCheckFailedException: version mismatch")
        self._state = replace(
            state,
            version=state.version + 1,
            updated_at=datetime.now(tz=JST).isoformat(),
        )
        return self._state

    def mark_processed(self, event_id: str) -> bool:
        key = event_key(event_id)
        if key in self.processed:
            return False
        self.processed.add(key)
        return True

    def acquire_lock(self, ttl_seconds: int, owner: str = "") -> str:
        now = time.time()
        if self._lock and self._lock[1] > now:
            raise LockNotAcquired("他の実行がロックを保持しています")
        token = f"{owner or 'test'}:{uuid.uuid4()}"
        self._lock = (token, now + ttl_seconds)
        return token

    def release_lock(self, token: str) -> None:
        if self._lock and self._lock[0] == token:
            self._lock = None

    def record_audit(self, entry: Mapping[str, Any]) -> None:
        self.audits.append(dict(entry))

    def get_feed_cache(self, url: str) -> Mapping[str, Any]:
        return self._feed_cache.get(url, {})

    def put_feed_cache(self, url: str, etag: str, last_modified: str) -> None:
        self._feed_cache[url] = {"etag": etag, "last_modified": last_modified}


def build_store(config: Any, table: Any | None = None) -> StateStore:
    """設定に応じて DynamoDB / インメモリのどちらかを返す。"""
    if table is not None:
        return DynamoStateStore(table, config.service_name)
    if config.dry_run and not config.aws.state_table:
        return InMemoryStateStore(config.service_name)
    import boto3  # type: ignore

    resource = boto3.resource("dynamodb", region_name=config.region)
    return DynamoStateStore(resource.Table(config.aws.state_table), config.service_name)
