"""AWS制御。SKILL.md Phase 4 / Phase 6「Dry Run」「スケールAPI失敗」「一部だけ成功」。"""
from __future__ import annotations

import pytest

from conftest import make_config
from fakes import FakeAutoScaling, FakeEcs, FakeElbv2, FakeRds
from jma_pre_scale.controller import AwsClients, ScalingController
from jma_pre_scale.models import ScaleLevel, ScalingTarget
from jma_pre_scale.rules import target_for_level

TARGET = ScalingTarget(ecs_desired_count=15, ecs_min_capacity=15,
                       aurora_min_acu=8.0, aurora_max_acu=32.0)


def build(dry_run: bool = False, **fake_kwargs):
    config = make_config(dry_run=dry_run)
    clients = AwsClients(
        ecs=fake_kwargs.get("ecs", FakeEcs()),
        application_autoscaling=fake_kwargs.get("aas", FakeAutoScaling()),
        rds=fake_kwargs.get("rds", FakeRds()),
        elbv2=fake_kwargs.get("elbv2", FakeElbv2()),
    )
    return ScalingController(config, clients), clients


# ------------------------------------------------------------------ Dry Run


def test_DryRunでは実APIを一切呼ばない():
    ecs, aas, rds = FakeEcs(), FakeAutoScaling(), FakeRds()
    controller, _ = build(dry_run=True, ecs=ecs, aas=aas, rds=rds)
    result = controller.apply(TARGET)

    assert result.status == "DRY_RUN"
    assert ecs.desired == 2, "Dry Run で希望数が変更されてはならない"
    assert aas.min_capacity == 2
    assert rds.min_acu == 0.5
    assert not any(c[0] == "update_service" for c in ecs.calls)
    assert not any(c[0] == "modify_db_cluster" for c in rds.calls)


def test_DryRunでもヘルスチェックは安全に通る():
    controller, _ = build(dry_run=True)
    assert controller.health_check(TARGET)["healthy"] is True


# ------------------------------------------------------------------ 拡張


def test_拡張はAurora_MinCapacity_Desiredの順で適用される():
    ecs, aas, rds = FakeEcs(), FakeAutoScaling(), FakeRds()
    controller, _ = build(ecs=ecs, aas=aas, rds=rds)
    result = controller.apply(TARGET)

    assert result.status == "SUCCEEDED"
    order = [s.resource for s in result.steps]
    assert order == ["aurora", "application-autoscaling", "ecs"], (
        "DBを先に広げ、MinCapacityを上げてからDesiredを上げる必要がある"
    )
    assert rds.min_acu == 8.0
    assert aas.min_capacity == 15
    assert ecs.desired == 15


def test_縮小は逆順で適用される():
    ecs, aas, rds = FakeEcs(desired=40), FakeAutoScaling(min_capacity=40), FakeRds(min_acu=16)
    controller, _ = build(ecs=ecs, aas=aas, rds=rds)
    result = controller.apply(TARGET, scale_in=True)

    order = [s.resource for s in result.steps]
    assert order == ["ecs", "application-autoscaling", "aurora"]
    assert ecs.desired == 15


def test_同じ目標を再適用しても副作用がない():
    ecs, aas, rds = FakeEcs(), FakeAutoScaling(), FakeRds()
    controller, _ = build(ecs=ecs, aas=aas, rds=rds)
    controller.apply(TARGET)
    update_count = len([c for c in ecs.calls if c[0] == "update_service"])

    second = controller.apply(TARGET)
    assert second.status == "SUCCEEDED"
    assert len([c for c in ecs.calls if c[0] == "update_service"]) == update_count, (
        "冪等: 既に目標値ならAPIを再度呼ばない"
    )
    assert all(s.skipped for s in second.steps)


def test_AuroraのMaxACUは縮小されない():
    rds = FakeRds(min_acu=16.0, max_acu=64.0)
    controller, _ = build(rds=rds)
    controller.apply(ScalingTarget(5, 5, 2.0, 16.0), scale_in=True)
    assert rds.max_acu == 64.0, "MaxACUを下げると接続断のリスクがある"
    assert rds.min_acu == 2.0


# -------------------------------------------------------------- 異常系


def test_ECS更新に失敗しても他リソースの結果は保持される():
    ecs = FakeEcs(fail_on_update=RuntimeError("AccessDenied"))
    aas, rds = FakeAutoScaling(), FakeRds()
    controller, _ = build(ecs=ecs, aas=aas, rds=rds)
    result = controller.apply(TARGET)

    assert result.status == "PARTIAL"
    assert rds.min_acu == 8.0, "先に成功した拡張は巻き戻さない"
    assert aas.min_capacity == 15
    assert len(result.failed) == 1


def test_全リソースの操作に失敗するとFAILEDになる():
    controller, _ = build(
        ecs=FakeEcs(fail_on_update=RuntimeError("boom")),
        aas=FakeAutoScaling(fail_on_register=RuntimeError("boom")),
        rds=FakeRds(fail_on_modify=RuntimeError("boom")),
    )
    result = controller.apply(TARGET)
    assert result.status == "FAILED"
    assert len(result.failed) == 3


def test_未設定のリソースはスキップされる():
    config = make_config(dry_run=False, aws_resources={"aurora_cluster_id": ""})
    controller = ScalingController(
        config, AwsClients(ecs=FakeEcs(), application_autoscaling=FakeAutoScaling(), rds=FakeRds())
    )
    result = controller.apply(TARGET)
    aurora_step = [s for s in result.steps if s.resource == "aurora"][0]
    assert aurora_step.skipped is True
    assert result.status == "SUCCEEDED"


# ---------------------------------------------------------- ヘルスチェック


def test_起動途中はin_progressになる():
    controller, _ = build(ecs=FakeEcs(desired=15, running=5))
    result = controller.health_check(TARGET)
    assert result["healthy"] is True
    assert result["in_progress"] is True


def test_希望数が目標に届いていなければunhealthy():
    controller, _ = build(ecs=FakeEcs(desired=2, running=2))
    result = controller.health_check(TARGET)
    assert result["healthy"] is False


def test_ALBにhealthyターゲットが無ければunhealthy():
    config = make_config(
        dry_run=False,
        aws_resources={"alb_target_group_arn": "arn:aws:elasticloadbalancing:x:1:targetgroup/t/1"},
    )
    controller = ScalingController(
        config,
        AwsClients(ecs=FakeEcs(desired=15, running=15), elbv2=FakeElbv2(total=3, healthy=0)),
    )
    result = controller.health_check(TARGET)
    assert result["healthy"] is False
    assert "alb_reason" in result["details"]


def test_操作前後の容量を記録できる():
    controller, _ = build()
    before = controller.describe_current()
    assert before["ecs"]["desiredCount"] == 2
    assert before["application_autoscaling"]["MinCapacity"] == 2
    assert before["aurora"]["ServerlessV2ScalingConfiguration"]["MinCapacity"] == 0.5
