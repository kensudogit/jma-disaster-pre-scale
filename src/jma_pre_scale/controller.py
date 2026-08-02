"""AWSリソース制御。SKILL.md Phase 4。

対象:
  - ECS/Fargate      : Application Auto Scaling の MinCapacity と Desired Count
  - Aurora Serverless v2 : ServerlessV2ScalingConfiguration の最小/最大ACU

適用順序が重要:
  拡張時は「DB -> ECS最小容量 -> ECS希望数」の順。
    DBの限界を無視してWeb層だけ増やさない(SKILL.md 禁止事項)。
    MinCapacity を先に上げないと、Target Tracking が直後に縮小し得る。
  縮小時は逆順。「ECS希望数 -> ECS最小容量 -> DB」。

すべて絶対値で指定するため、同じ入力を何度適用しても結果は同じ(冪等)。
一部だけ成功した場合もロールバック(=縮小)はしない。現在容量を維持し、
失敗を通知して人間の判断に委ねる。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .config import Config
from .models import ScalingTarget

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    resource: str
    action: str
    ok: bool
    detail: str = ""
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None
    skipped: bool = False


@dataclass
class ApplyResult:
    dry_run: bool
    target: ScalingTarget
    steps: list[StepResult] = field(default_factory=list)

    @property
    def failed(self) -> list[StepResult]:
        return [s for s in self.steps if not s.ok and not s.skipped]

    @property
    def status(self) -> str:
        if self.dry_run:
            return "DRY_RUN"
        if not self.failed:
            return "SUCCEEDED"
        if any(s.ok for s in self.steps):
            return "PARTIAL"
        return "FAILED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dry_run": self.dry_run,
            "target": self.target.to_dict(),
            "steps": [
                {
                    "resource": s.resource,
                    "action": s.action,
                    "ok": s.ok,
                    "skipped": s.skipped,
                    "detail": s.detail,
                    "before": s.before,
                    "after": s.after,
                }
                for s in self.steps
            ],
        }


@dataclass
class AwsClients:
    """必要なクライアントだけを保持する。テストではフェイクを渡す。"""

    ecs: Any = None
    application_autoscaling: Any = None
    rds: Any = None
    elbv2: Any = None

    @classmethod
    def build(cls, region: str) -> "AwsClients":
        import boto3  # type: ignore

        return cls(
            ecs=boto3.client("ecs", region_name=region),
            application_autoscaling=boto3.client("application-autoscaling", region_name=region),
            rds=boto3.client("rds", region_name=region),
            elbv2=boto3.client("elbv2", region_name=region),
        )


class ScalingController:
    def __init__(self, config: Config, clients: AwsClients | None = None) -> None:
        self._config = config
        self._clients = clients or AwsClients()

    # -------------------------------------------------------------- 現況取得
    def describe_current(self) -> dict[str, Any]:
        """操作前の容量を記録するために現況を取る。失敗しても致命ではない。"""
        aws = self._config.aws
        current: dict[str, Any] = {}
        ecs = self._clients.ecs
        if ecs and aws.ecs_cluster and aws.ecs_service:
            try:
                resp = ecs.describe_services(cluster=aws.ecs_cluster, services=[aws.ecs_service])
                services = resp.get("services") or []
                if services:
                    svc = services[0]
                    current["ecs"] = {
                        "desiredCount": svc.get("desiredCount"),
                        "runningCount": svc.get("runningCount"),
                        "pendingCount": svc.get("pendingCount"),
                        "status": svc.get("status"),
                    }
            except Exception as exc:  # noqa: BLE001
                current["ecs_error"] = str(exc)

        aas = self._clients.application_autoscaling
        resource_id = aws.resolved_scalable_resource_id()
        if aas and resource_id:
            try:
                resp = aas.describe_scalable_targets(
                    ServiceNamespace="ecs",
                    ResourceIds=[resource_id],
                    ScalableDimension="ecs:service:DesiredCount",
                )
                targets = resp.get("ScalableTargets") or []
                if targets:
                    current["application_autoscaling"] = {
                        "MinCapacity": targets[0].get("MinCapacity"),
                        "MaxCapacity": targets[0].get("MaxCapacity"),
                    }
            except Exception as exc:  # noqa: BLE001
                current["application_autoscaling_error"] = str(exc)

        rds = self._clients.rds
        if rds and aws.aurora_cluster_id:
            try:
                resp = rds.describe_db_clusters(DBClusterIdentifier=aws.aurora_cluster_id)
                clusters = resp.get("DBClusters") or []
                if clusters:
                    cluster = clusters[0]
                    current["aurora"] = {
                        "Status": cluster.get("Status"),
                        "ServerlessV2ScalingConfiguration":
                            cluster.get("ServerlessV2ScalingConfiguration"),
                        "ReaderCount": len(
                            [m for m in cluster.get("DBClusterMembers", [])
                             if not m.get("IsClusterWriter")]
                        ),
                    }
            except Exception as exc:  # noqa: BLE001
                current["aurora_error"] = str(exc)
        return current

    # -------------------------------------------------------------- 適用
    def apply(self, target: ScalingTarget, *, scale_in: bool = False) -> ApplyResult:
        """目標容量を適用する。dry_run では一切のAPIを呼ばない。"""
        cfg = self._config
        result = ApplyResult(dry_run=cfg.dry_run, target=target)

        if cfg.dry_run:
            before = self.describe_current() if self._clients.ecs else {}
            result.steps.append(
                StepResult(
                    resource="all",
                    action="dry-run",
                    ok=True,
                    skipped=True,
                    detail="Dry Run のため実APIは呼び出していません",
                    before=before or None,
                    after=target.to_dict(),
                )
            )
            logger.info("DRY RUN target=%s", target.to_dict())
            return result

        steps = (
            [self._scale_ecs_desired, self._scale_ecs_min_capacity, self._scale_aurora]
            if scale_in
            else [self._scale_aurora, self._scale_ecs_min_capacity, self._scale_ecs_desired]
        )
        for step in steps:
            try:
                result.steps.append(step(target))
            except Exception as exc:  # noqa: BLE001
                # 一部失敗でも後続を止めない。縮小方向のロールバックは行わない。
                logger.exception("scaling step failed")
                result.steps.append(
                    StepResult(resource=step.__name__, action="apply", ok=False, detail=str(exc))
                )
        return result

    # -------------------------------------------------------------- 個別操作
    def _scale_aurora(self, target: ScalingTarget) -> StepResult:
        aws = self._config.aws
        rds = self._clients.rds
        if not rds or not aws.aurora_cluster_id:
            return StepResult("aurora", "modify_db_cluster", ok=True, skipped=True,
                              detail="Aurora未設定のためスキップ")
        before = rds.describe_db_clusters(DBClusterIdentifier=aws.aurora_cluster_id)
        current_cfg = (before.get("DBClusters") or [{}])[0].get(
            "ServerlessV2ScalingConfiguration", {}
        )
        # 縮小方向でも最小ACUは下げるだけ、最大ACUは下げない(接続断リスク回避)
        desired_max = max(
            float(target.aurora_max_acu), float(current_cfg.get("MaxCapacity") or 0)
        )
        if (
            float(current_cfg.get("MinCapacity") or -1) == float(target.aurora_min_acu)
            and float(current_cfg.get("MaxCapacity") or -1) == desired_max
        ):
            return StepResult("aurora", "modify_db_cluster", ok=True, skipped=True,
                              detail="既に目標ACUです", before=current_cfg, after=current_cfg)
        rds.modify_db_cluster(
            DBClusterIdentifier=aws.aurora_cluster_id,
            ServerlessV2ScalingConfiguration={
                "MinCapacity": float(target.aurora_min_acu),
                "MaxCapacity": float(desired_max),
            },
            ApplyImmediately=True,
        )
        return StepResult(
            "aurora", "modify_db_cluster", ok=True,
            detail=f"MinACU={target.aurora_min_acu} MaxACU={desired_max}",
            before=current_cfg,
            after={"MinCapacity": target.aurora_min_acu, "MaxCapacity": desired_max},
        )

    def _scale_ecs_min_capacity(self, target: ScalingTarget) -> StepResult:
        aws = self._config.aws
        aas = self._clients.application_autoscaling
        resource_id = aws.resolved_scalable_resource_id()
        if not aas or not resource_id:
            return StepResult("application-autoscaling", "register_scalable_target",
                              ok=True, skipped=True, detail="Auto Scaling未設定のためスキップ")
        described = aas.describe_scalable_targets(
            ServiceNamespace="ecs",
            ResourceIds=[resource_id],
            ScalableDimension="ecs:service:DesiredCount",
        )
        targets = described.get("ScalableTargets") or []
        before = (
            {"MinCapacity": targets[0].get("MinCapacity"),
             "MaxCapacity": targets[0].get("MaxCapacity")}
            if targets else {}
        )
        #  MaxCapacity は上限クランプ値まで確保しておき、下げない
        max_capacity = max(
            int(before.get("MaxCapacity") or 0),
            self._config.safety.absolute_max_ecs_tasks,
        )
        if (
            int(before.get("MinCapacity") or -1) == int(target.ecs_min_capacity)
            and int(before.get("MaxCapacity") or -1) == int(max_capacity)
        ):
            return StepResult("application-autoscaling", "register_scalable_target",
                              ok=True, skipped=True, detail="既に目標容量です",
                              before=before, after=before)
        aas.register_scalable_target(
            ServiceNamespace="ecs",
            ResourceId=resource_id,
            ScalableDimension="ecs:service:DesiredCount",
            MinCapacity=int(target.ecs_min_capacity),
            MaxCapacity=int(max_capacity),
        )
        return StepResult(
            "application-autoscaling", "register_scalable_target", ok=True,
            detail=f"MinCapacity={target.ecs_min_capacity} MaxCapacity={max_capacity}",
            before=before or None,
            after={"MinCapacity": target.ecs_min_capacity, "MaxCapacity": max_capacity},
        )

    def _scale_ecs_desired(self, target: ScalingTarget) -> StepResult:
        aws = self._config.aws
        ecs = self._clients.ecs
        if not ecs or not aws.ecs_cluster or not aws.ecs_service:
            return StepResult("ecs", "update_service", ok=True, skipped=True,
                              detail="ECS未設定のためスキップ")
        described = ecs.describe_services(cluster=aws.ecs_cluster, services=[aws.ecs_service])
        services = described.get("services") or []
        before = (
            {"desiredCount": services[0].get("desiredCount"),
             "runningCount": services[0].get("runningCount")}
            if services else {}
        )
        if before.get("desiredCount") == int(target.ecs_desired_count):
            return StepResult("ecs", "update_service", ok=True, skipped=True,
                              detail="既に目標希望数です", before=before, after=before)
        ecs.update_service(
            cluster=aws.ecs_cluster,
            service=aws.ecs_service,
            desiredCount=int(target.ecs_desired_count),
        )
        return StepResult(
            "ecs", "update_service", ok=True,
            detail=f"desiredCount={target.ecs_desired_count}",
            before=before or None,
            after={"desiredCount": target.ecs_desired_count},
        )

    # -------------------------------------------------------------- 健全性
    def health_check(self, target: ScalingTarget) -> dict[str, Any]:
        """拡張後のタスクが実際に起動しているかを確認する。

        SKILL.md Phase 6「ヘルスチェック失敗」の検知点。
        起動途中(running < desired)は failed ではなく in_progress として扱い、
        Step Functions 側で待ち直す。
        """
        aws = self._config.aws
        ecs = self._clients.ecs
        out: dict[str, Any] = {"healthy": True, "in_progress": False, "details": {}}

        if self._config.dry_run:
            out["details"]["dry_run"] = "Dry Run のためヘルスチェックをスキップしました"
            return out

        if ecs and aws.ecs_cluster and aws.ecs_service:
            resp = ecs.describe_services(cluster=aws.ecs_cluster, services=[aws.ecs_service])
            services = resp.get("services") or []
            if not services:
                out["healthy"] = False
                out["details"]["ecs"] = "サービスが見つかりません"
            else:
                svc = services[0]
                running = int(svc.get("runningCount") or 0)
                desired = int(svc.get("desiredCount") or 0)
                out["details"]["ecs"] = {
                    "runningCount": running,
                    "desiredCount": desired,
                    "pendingCount": svc.get("pendingCount"),
                }
                if desired < int(target.ecs_desired_count):
                    out["healthy"] = False
                    out["details"]["ecs_reason"] = "希望数が目標に達していません"
                elif running < desired:
                    out["in_progress"] = True

        elbv2 = self._clients.elbv2
        if elbv2 and aws.alb_target_group_arn:
            resp = elbv2.describe_target_health(TargetGroupArn=aws.alb_target_group_arn)
            descriptions = resp.get("TargetHealthDescriptions") or []
            healthy = [
                d for d in descriptions
                if (d.get("TargetHealth") or {}).get("State") == "healthy"
            ]
            out["details"]["alb"] = {
                "total": len(descriptions),
                "healthy": len(healthy),
            }
            if descriptions and not healthy:
                out["healthy"] = False
                out["details"]["alb_reason"] = "healthyなターゲットがありません"
            elif len(healthy) < len(descriptions):
                out["in_progress"] = True

        return out
