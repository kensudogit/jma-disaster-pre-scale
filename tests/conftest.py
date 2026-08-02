"""テスト共通のフィクスチャ。AWS への接続は一切行わない。"""
from __future__ import annotations

import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jma_pre_scale.config import Config, from_mapping  # noqa: E402
from jma_pre_scale.state import InMemoryStateStore  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


BASE_CONFIG: dict[str, Any] = {
    "service_name": "test-service",
    "region": "ap-northeast-1",
    "dry_run": True,
    "jma": {
        "feed_urls": ["https://www.data.jma.go.jp/developer/xml/feed/extra.xml"],
        "target_area_codes": ["130000", "13", "101"],
        "target_area_names": ["東京都", "東京湾内湾"],
        "supported_event_types": [
            "heavy_rain", "flood", "typhoon", "earthquake", "tsunami", "storm", "high_tide",
        ],
    },
    "scaling": {
        "level_0": {"ecs_desired_count": 2, "ecs_min_capacity": 2,
                    "aurora_min_acu": 0.5, "aurora_max_acu": 8},
        "level_1": {"ecs_desired_count": 5, "ecs_min_capacity": 5,
                    "aurora_min_acu": 2, "aurora_max_acu": 16},
        "level_2": {"ecs_desired_count": 15, "ecs_min_capacity": 15,
                    "aurora_min_acu": 8, "aurora_max_acu": 32},
        "level_3": {"ecs_desired_count": 40, "ecs_min_capacity": 40,
                    "aurora_min_acu": 16, "aurora_max_acu": 64},
        "cooldown_minutes": 120,
        "scale_in_step": 5,
        "absolute_max_capacity": 50,
    },
    "safety": {
        "require_manual_approval_for_level_3": True,
        "allow_automatic_scale_in": False,
        "baseline_reserve_tasks": 2,
        "absolute_max_aurora_acu": 64,
    },
    "aws_resources": {
        "ecs_cluster": "test-cluster",
        "ecs_service": "test-service",
        "aurora_cluster_id": "test-aurora",
        "state_table": "test-state-table",
    },
}


def make_config(**overrides: Any) -> Config:
    import copy

    raw = copy.deepcopy(BASE_CONFIG)

    def deep_merge(dst: dict, src: dict) -> dict:
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                deep_merge(dst[k], v)
            else:
                dst[k] = v
        return dst

    deep_merge(raw, overrides)
    cfg = from_mapping(raw)
    cfg.validate()
    return cfg


@pytest.fixture
def config() -> Config:
    return make_config()


@pytest.fixture
def store() -> InMemoryStateStore:
    return InMemoryStateStore("test-service")
