"""SKILL.md の最重要制約・禁止事項への適合を、実際の挙動で検証する。

ドキュメントの記述ではなく、コードが実際にその通り動くかを確かめる。
1件でも落ちたら本番投入してはならない。
"""
from __future__ import annotations

import pathlib
from datetime import timedelta

import pytest

from conftest import fixture_bytes, make_config
from fakes import FakeAutoScaling, FakeEcs, FakeRds
from jma_pre_scale.config import ConfigError
from jma_pre_scale.controller import AwsClients, ScalingController
from jma_pre_scale.models import Action, ScaleLevel
from jma_pre_scale.parser import parse_report
from jma_pre_scale.rules import decide, decide_on_feed_error, now_jst
from jma_pre_scale.state import ScaleState

ROOT = pathlib.Path(__file__).resolve().parents[1]


def ev(name):
    return parse_report(fixture_bytes(name), source_url=f"https://example/{name}")


# ============================================================ 最重要制約


def test_制約_既存アプリのコードやDBスキーマを変更するAPIを呼ばない():
    """許可されているのはコントロールプレーンの容量変更のみ。"""
    forbidden = [
        "register_task_definition", "put_object", "execute_statement",
        "modify_db_parameter_group", "create_db_instance", "delete_",
        "modify_listener", "modify_target_group", "put_rule",
        "update_function_code", "execute_command",
    ]
    src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "src").rglob("*.py")
    )
    hits = [f for f in forbidden if f + "(" in src]
    assert not hits, f"既存システムを変更しうるAPI呼び出しが含まれています: {hits}"


def test_制約_XML取得失敗時は縮小せず現在容量を維持する(config):
    for level in (ScaleLevel.LEVEL_1, ScaleLevel.LEVEL_2, ScaleLevel.LEVEL_3):
        d = decide_on_feed_error("timeout", config=config,
                                 state=ScaleState(current_level=level))
        assert d.action is Action.HOLD
        assert d.level is level
        assert d.target.ecs_desired_count == config.target_for(level).ecs_desired_count


def test_制約_解除受信でも即時縮小しない(config):
    for level in (ScaleLevel.LEVEL_1, ScaleLevel.LEVEL_2, ScaleLevel.LEVEL_3):
        for fixture in ("warning_tokyo_release.xml", "warning_tokyo_cancel.xml"):
            d = decide(ev(fixture), config=config, state=ScaleState(current_level=level))
            assert d.action is Action.HOLD, f"{fixture} @ {level.name}"
            assert d.level is level


def test_制約_クールダウン時間が確保される():
    config = make_config(safety={"allow_automatic_scale_in": True})
    #  解除直後(クールダウン未設定) -> 縮小しない
    d = decide(ev("warning_tokyo_release.xml"), config=config,
               state=ScaleState(current_level=ScaleLevel.LEVEL_3))
    assert d.action is Action.HOLD
    #  クールダウン中 -> 縮小しない
    d = decide([], config=config, state=ScaleState(
        current_level=ScaleLevel.LEVEL_3,
        cooldown_until=now_jst() + timedelta(minutes=1)))
    assert d.action is Action.HOLD


def test_制約_常時予備容量をゼロにできない():
    with pytest.raises(ConfigError):
        make_config(safety={"baseline_reserve_tasks": 0})
    #  設定で0を書いてもクランプで押し戻される
    config = make_config(
        scaling={"level_0": {"ecs_desired_count": 0, "ecs_min_capacity": 0,
                             "aurora_min_acu": 0.5, "aurora_max_acu": 8}})
    from jma_pre_scale.rules import target_for_level
    assert target_for_level(ScaleLevel.LEVEL_0, config).ecs_desired_count >= 1


def test_制約_本番変更前にDryRunが提供される():
    config = make_config(dry_run=True)
    ecs, aas, rds = FakeEcs(), FakeAutoScaling(), FakeRds()
    controller = ScalingController(
        config, AwsClients(ecs=ecs, application_autoscaling=aas, rds=rds))
    from jma_pre_scale.rules import target_for_level
    controller.apply(target_for_level(ScaleLevel.LEVEL_3, config))
    assert (ecs.desired, aas.min_capacity, rds.min_acu) == (2, 2, 0.5)


def test_制約_ロールバックが可能である():
    """手動オーバーライドでいつでも任意のレベルへ戻せる。"""
    config = make_config()
    d = decide([], config=config,
               state=ScaleState(current_level=ScaleLevel.LEVEL_3, forced_level=0))
    assert d.action is Action.SCALE_IN
    assert d.level is ScaleLevel.LEVEL_0


def test_制約_配布時の既定はDryRunである():
    import yaml
    for name in ("config.yaml", "config.example.yaml"):
        raw = yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))
        assert raw["dry_run"] is True, name
    tfvars = (ROOT / "terraform" / "terraform.tfvars.example").read_text(encoding="utf-8")
    assert "dry_run = true" in tfvars


# ============================================================ 禁止事項


def test_禁止_未検証XMLをそのままインフラ操作へ接続しない():
    from jma_pre_scale.parser import XmlValidationError
    for bad in ("malformed.xml", "xxe.xml"):
        with pytest.raises(XmlValidationError):
            parse_report(fixture_bytes(bad))


def test_禁止_XML受信だけで無制限スケールしない():
    config = make_config(
        scaling={"level_3": {"ecs_desired_count": 100000, "ecs_min_capacity": 100000,
                             "aurora_min_acu": 1000, "aurora_max_acu": 1000},
                 "absolute_max_capacity": 50})
    d = decide(ev("warning_tokyo_emergency.xml"), config=config, state=ScaleState())
    assert d.target.ecs_desired_count == 50
    assert d.target.aurora_max_acu == 64


def test_禁止_地震時にゼロ台起動だけに依存しない():
    config = make_config()
    assert config.safety.baseline_reserve_tasks >= 1
    from jma_pre_scale.rules import target_for_level
    assert target_for_level(ScaleLevel.LEVEL_0, config).ecs_desired_count >= 1


def test_禁止_DB限界を無視してWeb層だけ増やさない():
    config = make_config(dry_run=False)
    ecs, aas, rds = FakeEcs(), FakeAutoScaling(), FakeRds()
    controller = ScalingController(
        config, AwsClients(ecs=ecs, application_autoscaling=aas, rds=rds))
    from jma_pre_scale.rules import target_for_level
    result = controller.apply(target_for_level(ScaleLevel.LEVEL_3, config))
    order = [s.resource for s in result.steps]
    assert order.index("aurora") < order.index("ecs"), "DBを先に拡張する必要がある"
    assert rds.min_acu > 0.5, "DB層が拡張されていない"


def test_禁止_解除受信直後に平時容量へ戻さない():
    config = make_config(safety={"allow_automatic_scale_in": True})
    state = ScaleState(current_level=ScaleLevel.LEVEL_3,
                       cooldown_until=now_jst() - timedelta(minutes=1))
    d = decide([], config=config, state=state)
    assert d.action is Action.SCALE_IN
    level_0 = config.target_for(ScaleLevel.LEVEL_0).ecs_desired_count
    assert d.target.ecs_desired_count > level_0, "一気に平時容量へ戻してはならない"


def test_禁止_訓練電文でインフラを操作しない(config):
    d = decide(ev("warning_tokyo_drill.xml"), config=config, state=ScaleState())
    assert d.action is Action.NOOP
    assert d.level is ScaleLevel.LEVEL_0


# ============================================================ 安全設計


def test_安全_同一イベントの多重実行を防ぐ(store):
    assert store.mark_processed("e1") is True
    assert store.mark_processed("e1") is False


def test_安全_操作前後の容量を記録する():
    config = make_config(dry_run=False)
    controller = ScalingController(config, AwsClients(
        ecs=FakeEcs(), application_autoscaling=FakeAutoScaling(), rds=FakeRds()))
    from jma_pre_scale.rules import target_for_level
    result = controller.apply(target_for_level(ScaleLevel.LEVEL_2, config))
    applied = [s for s in result.steps if not s.skipped]
    assert applied
    for step in applied:
        assert step.before is not None and step.after is not None, step.resource


def test_安全_失敗時は縮小せず現在容量を維持する():
    config = make_config(dry_run=False)
    ecs = FakeEcs(desired=40, fail_on_update=RuntimeError("AccessDenied"))
    rds = FakeRds(min_acu=16.0, max_acu=64.0)
    controller = ScalingController(config, AwsClients(
        ecs=ecs, application_autoscaling=FakeAutoScaling(min_capacity=40), rds=rds))
    from jma_pre_scale.rules import target_for_level
    result = controller.apply(target_for_level(ScaleLevel.LEVEL_1, config), scale_in=True)
    assert result.status in ("PARTIAL", "FAILED")
    assert ecs.desired == 40, "失敗時に縮小してはならない"


def test_安全_手動オーバーライドが最優先される(config):
    d = decide(ev("warning_tokyo_emergency.xml"), config=config,
               state=ScaleState(current_level=ScaleLevel.LEVEL_0, automation_disabled=True))
    assert d.action is Action.HOLD
    assert d.level is ScaleLevel.LEVEL_0


def test_安全_気象庁への過剰アクセスを構造的に防ぐ():
    """条件付きGET と 重複排除 が実装から外せないことを確認する。"""
    feed_src = (ROOT / "src" / "jma_pre_scale" / "feed.py").read_text(encoding="utf-8")
    assert "If-None-Match" in feed_src and "If-Modified-Since" in feed_src
    orch = (ROOT / "src" / "jma_pre_scale" / "orchestrator.py").read_text(encoding="utf-8")
    assert "get_feed_cache" in orch and "mark_processed" in orch
    tfvars = (ROOT / "terraform" / "variables.tf").read_text(encoding="utf-8")
    assert "poll_interval_minutes >= 1" in tfvars


def test_安全_許可外ホストへは接続しない():
    from jma_pre_scale.feed import FeedError, fetch
    for url in ("https://evil.example.com/f.xml", "http://www.data.jma.go.jp/f.xml"):
        with pytest.raises(FeedError):
            fetch(url)
