"""AWS SDK のフェイク。実APIを一切呼ばずに Phase 6 の異常系を再現する。"""
from __future__ import annotations

from typing import Any


class FakeEcs:
    def __init__(self, desired: int = 2, running: int | None = None,
                 fail_on_update: Exception | None = None) -> None:
        self.desired = desired
        self.running = desired if running is None else running
        self.fail_on_update = fail_on_update
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def describe_services(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe_services", kwargs))
        return {
            "services": [
                {"desiredCount": self.desired, "runningCount": self.running,
                 "pendingCount": max(0, self.desired - self.running), "status": "ACTIVE"}
            ]
        }

    def update_service(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("update_service", kwargs))
        if self.fail_on_update:
            raise self.fail_on_update
        self.desired = kwargs["desiredCount"]
        return {"service": {"desiredCount": self.desired}}


class FakeAutoScaling:
    def __init__(self, min_capacity: int = 2, max_capacity: int = 50,
                 fail_on_register: Exception | None = None) -> None:
        self.min_capacity = min_capacity
        self.max_capacity = max_capacity
        self.fail_on_register = fail_on_register
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def describe_scalable_targets(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe_scalable_targets", kwargs))
        return {
            "ScalableTargets": [
                {"MinCapacity": self.min_capacity, "MaxCapacity": self.max_capacity}
            ]
        }

    def register_scalable_target(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("register_scalable_target", kwargs))
        if self.fail_on_register:
            raise self.fail_on_register
        self.min_capacity = kwargs["MinCapacity"]
        self.max_capacity = kwargs["MaxCapacity"]
        return {}


class FakeRds:
    def __init__(self, min_acu: float = 0.5, max_acu: float = 8.0,
                 fail_on_modify: Exception | None = None) -> None:
        self.min_acu = min_acu
        self.max_acu = max_acu
        self.fail_on_modify = fail_on_modify
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def describe_db_clusters(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("describe_db_clusters", kwargs))
        return {
            "DBClusters": [
                {
                    "Status": "available",
                    "ServerlessV2ScalingConfiguration": {
                        "MinCapacity": self.min_acu,
                        "MaxCapacity": self.max_acu,
                    },
                    "DBClusterMembers": [{"IsClusterWriter": True}],
                }
            ]
        }

    def modify_db_cluster(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("modify_db_cluster", kwargs))
        if self.fail_on_modify:
            raise self.fail_on_modify
        cfg = kwargs["ServerlessV2ScalingConfiguration"]
        self.min_acu = cfg["MinCapacity"]
        self.max_acu = cfg["MaxCapacity"]
        return {}


class FakeElbv2:
    def __init__(self, total: int = 2, healthy: int = 2) -> None:
        self.total = total
        self.healthy = healthy

    def describe_target_health(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "TargetHealthDescriptions": [
                {"TargetHealth": {"State": "healthy" if i < self.healthy else "unhealthy"}}
                for i in range(self.total)
            ]
        }


class FakeResponse:
    """urllib のレスポンス相当。feed.fetch のテストに使う。"""

    def __init__(self, body: bytes, status: int = 200,
                 headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {"Content-Type": "application/xml"}

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False
