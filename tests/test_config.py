"""設定検証。事故につながる設定は起動時に落とす。"""
from __future__ import annotations

import json
import pathlib

import pytest

from conftest import BASE_CONFIG, make_config
from jma_pre_scale.config import ConfigError, from_mapping, load
from jma_pre_scale.models import ScaleLevel

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_同梱の設定例が検証を通る():
    try:
        import yaml  # noqa: F401
    except ImportError:
        pytest.skip("PyYAML 未導入")
    cfg = load(ROOT / "config" / "config.example.yaml")
    assert cfg.dry_run is True, "配布時の既定は必ず Dry Run"
    assert cfg.safety.baseline_reserve_tasks >= 1


def test_レベル定義が欠けていると起動できない():
    raw = {k: v for k, v in BASE_CONFIG.items()}
    raw["scaling"] = {"level_0": BASE_CONFIG["scaling"]["level_0"]}
    with pytest.raises(ConfigError, match="level_1"):
        from_mapping(raw).validate()


def test_httpのフィードは拒否される():
    with pytest.raises(ConfigError, match="https"):
        make_config(jma={"feed_urls": ["http://www.data.jma.go.jp/x.xml"]})


def test_常時予備容量ゼロは拒否される():
    with pytest.raises(ConfigError, match="baseline_reserve_tasks"):
        make_config(safety={"baseline_reserve_tasks": 0})


def test_レベルが上がって容量が下がる設定は拒否される():
    with pytest.raises(ConfigError, match="下回"):
        make_config(scaling={"level_2": {"ecs_desired_count": 1, "ecs_min_capacity": 1,
                                         "aurora_min_acu": 1, "aurora_max_acu": 8}})


def test_本番モードではリソース名が必須():
    with pytest.raises(ConfigError, match="ecs_cluster"):
        make_config(dry_run=False, aws_resources={"ecs_cluster": "", "ecs_service": ""})


def test_ScalableResourceIdが自動で組み立てられる(config):
    assert config.aws.resolved_scalable_resource_id() == "service/test-cluster/test-service"


def test_CONFIG_JSON環境変数から読み込める():
    import os

    os.environ["CONFIG_JSON"] = json.dumps(BASE_CONFIG)
    try:
        cfg = load()
        assert cfg.service_name == "test-service"
        assert cfg.target_for(ScaleLevel.LEVEL_2).ecs_desired_count == 15
    finally:
        del os.environ["CONFIG_JSON"]


def test_存在しない設定ファイルはエラーになる():
    with pytest.raises(ConfigError, match="見つかりません"):
        load("/nonexistent/config.yaml")
