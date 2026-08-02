"""設定ロードと検証。

SKILL.md「入力情報」の項目をこのファイルで一元管理する。
不足情報は例外にせず、安全側(dry_run維持 / HOLD)に倒す既定値を持つ。
YAMLが読めない実行環境(Lambda素のランタイム)でもJSONで動くようにしている。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .models import ScaleLevel, ScalingTarget

DEFAULT_FEEDS = (
    "https://www.data.jma.go.jp/developer/xml/feed/extra.xml",
    "https://www.data.jma.go.jp/developer/xml/feed/eqvol.xml",
)


class ConfigError(ValueError):
    """設定不備。起動時に必ず検知させる。"""


@dataclass(frozen=True)
class JmaConfig:
    feed_urls: tuple[str, ...] = DEFAULT_FEEDS
    request_timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    user_agent: str = "jma-disaster-pre-scale/1.0 (+ops@example.jp)"
    max_feed_bytes: int = 8 * 1024 * 1024
    max_document_bytes: int = 4 * 1024 * 1024
    max_documents_per_run: int = 20
    target_area_codes: tuple[str, ...] = ()
    #  名称による補助一致(コード体系が予報区/府県/津波予報区で異なるため)
    target_area_names: tuple[str, ...] = ()
    supported_event_types: tuple[str, ...] = (
        "heavy_rain", "flood", "typhoon", "earthquake", "tsunami",
        "storm", "snow", "high_tide", "landslide",
    )
    accept_drill_messages: bool = False


@dataclass(frozen=True)
class SafetyConfig:
    require_manual_approval_for_level_3: bool = True
    hold_on_feed_error: bool = True
    allow_automatic_scale_in: bool = False
    cooldown_minutes: int = 120
    scale_in_step: int = 5
    absolute_max_ecs_tasks: int = 50
    absolute_max_aurora_acu: float = 64.0
    #  地震等の突発災害に備える常時予備容量(SKILL.md 最重要制約)
    baseline_reserve_tasks: int = 2
    lock_ttl_seconds: int = 900
    health_check_timeout_seconds: int = 600


@dataclass(frozen=True)
class AwsResources:
    ecs_cluster: str = ""
    ecs_service: str = ""
    #  Application Auto Scaling の ResourceId: service/<cluster>/<service>
    ecs_scalable_resource_id: str = ""
    aurora_cluster_id: str = ""
    state_table: str = "jma-disaster-pre-scale-state"
    notification_topic_arn: str = ""
    approval_topic_arn: str = ""
    state_machine_arn: str = ""
    alb_target_group_arn: str = ""

    def resolved_scalable_resource_id(self) -> str:
        if self.ecs_scalable_resource_id:
            return self.ecs_scalable_resource_id
        if self.ecs_cluster and self.ecs_service:
            return f"service/{self.ecs_cluster}/{self.ecs_service}"
        return ""


@dataclass(frozen=True)
class Config:
    service_name: str = "disaster-access-system"
    region: str = "ap-northeast-1"
    dry_run: bool = True
    poll_interval_minutes: int = 1
    jma: JmaConfig = field(default_factory=JmaConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    aws: AwsResources = field(default_factory=AwsResources)
    scaling: Mapping[str, ScalingTarget] = field(default_factory=dict)
    #  災害種別ごとの重大度→レベルの上書き(未指定は既定ルール)
    severity_overrides: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    def target_for(self, level: ScaleLevel) -> ScalingTarget:
        try:
            return self.scaling[level.key]
        except KeyError as exc:  # pragma: no cover - 設定検証で弾かれる
            raise ConfigError(f"scaling.{level.key} が未定義です") from exc

    def validate(self) -> None:
        errors: list[str] = []
        for level in ScaleLevel:
            if level.key not in self.scaling:
                errors.append(f"scaling.{level.key} が未定義です")
        if not self.jma.feed_urls:
            errors.append("jma.feed_urls が空です")
        for url in self.jma.feed_urls:
            if not url.startswith("https://"):
                errors.append(f"jma.feed_urls は https のみ許可: {url}")
        if self.safety.absolute_max_ecs_tasks < 1:
            errors.append("safety.absolute_max_ecs_tasks は1以上である必要があります")
        if self.safety.baseline_reserve_tasks < 1:
            errors.append(
                "safety.baseline_reserve_tasks は1以上である必要があります"
                "(地震等の突発災害でゼロ台起動に依存しないため)"
            )
        # 単調増加性: レベルが上がって容量が下がる設定は事故のもと
        prev = -1
        for level in ScaleLevel:
            t = self.scaling.get(level.key)
            if t is None:
                continue
            if t.ecs_desired_count < prev:
                errors.append(f"scaling.{level.key} の容量が下位レベルを下回っています")
            prev = t.ecs_desired_count
        if not self.dry_run:
            if not self.aws.ecs_cluster or not self.aws.ecs_service:
                errors.append("dry_run=false では aws.ecs_cluster / ecs_service が必須です")
            if not self.aws.state_table:
                errors.append("dry_run=false では aws.state_table が必須です")
        if errors:
            raise ConfigError("設定検証エラー:\n- " + "\n- ".join(errors))


def _target_from(raw: Mapping[str, Any]) -> ScalingTarget:
    desired = int(raw["ecs_desired_count"])
    return ScalingTarget(
        ecs_desired_count=desired,
        ecs_min_capacity=int(raw.get("ecs_min_capacity", desired)),
        aurora_min_acu=float(raw.get("aurora_min_acu", 0.5)),
        aurora_max_acu=float(raw.get("aurora_max_acu", 16.0)),
    )


def from_mapping(raw: Mapping[str, Any]) -> Config:
    """dict(YAML/JSON由来)から Config を組み立てる。"""
    jma_raw = dict(raw.get("jma") or {})
    feed_urls = jma_raw.get("feed_urls")
    if isinstance(feed_urls, str):
        feed_urls = [feed_urls]
    jma = JmaConfig(
        feed_urls=tuple(feed_urls) if feed_urls else DEFAULT_FEEDS,
        request_timeout_seconds=float(jma_raw.get("request_timeout_seconds", 10.0)),
        max_retries=int(jma_raw.get("max_retries", 2)),
        retry_backoff_seconds=float(jma_raw.get("retry_backoff_seconds", 1.0)),
        user_agent=str(jma_raw.get("user_agent", JmaConfig.user_agent)),
        max_feed_bytes=int(jma_raw.get("max_feed_bytes", JmaConfig.max_feed_bytes)),
        max_document_bytes=int(jma_raw.get("max_document_bytes", JmaConfig.max_document_bytes)),
        max_documents_per_run=int(jma_raw.get("max_documents_per_run", 20)),
        target_area_codes=tuple(str(c) for c in (jma_raw.get("target_area_codes") or ())),
        target_area_names=tuple(str(c) for c in (jma_raw.get("target_area_names") or ())),
        supported_event_types=tuple(
            jma_raw.get("supported_event_types") or JmaConfig.supported_event_types
        ),
        accept_drill_messages=bool(jma_raw.get("accept_drill_messages", False)),
    )

    safety_raw = dict(raw.get("safety") or {})
    scaling_raw = dict(raw.get("scaling") or {})
    safety = SafetyConfig(
        require_manual_approval_for_level_3=bool(
            safety_raw.get("require_manual_approval_for_level_3", True)
        ),
        hold_on_feed_error=bool(safety_raw.get("hold_on_feed_error", True)),
        allow_automatic_scale_in=bool(safety_raw.get("allow_automatic_scale_in", False)),
        cooldown_minutes=int(scaling_raw.get("cooldown_minutes",
                                             safety_raw.get("cooldown_minutes", 120))),
        scale_in_step=int(scaling_raw.get("scale_in_step",
                                          safety_raw.get("scale_in_step", 5))),
        absolute_max_ecs_tasks=int(
            scaling_raw.get("absolute_max_capacity",
                            safety_raw.get("absolute_max_ecs_tasks", 50))
        ),
        absolute_max_aurora_acu=float(safety_raw.get("absolute_max_aurora_acu", 64.0)),
        baseline_reserve_tasks=int(safety_raw.get("baseline_reserve_tasks", 2)),
        lock_ttl_seconds=int(safety_raw.get("lock_ttl_seconds", 900)),
        health_check_timeout_seconds=int(safety_raw.get("health_check_timeout_seconds", 600)),
    )

    aws_raw = dict(raw.get("aws_resources") or raw.get("aws") or {})
    aws = AwsResources(
        ecs_cluster=str(aws_raw.get("ecs_cluster", "")),
        ecs_service=str(aws_raw.get("ecs_service", "")),
        ecs_scalable_resource_id=str(aws_raw.get("ecs_scalable_resource_id", "")),
        aurora_cluster_id=str(aws_raw.get("aurora_cluster_id", "")),
        state_table=str(aws_raw.get("state_table", "jma-disaster-pre-scale-state")),
        notification_topic_arn=str(aws_raw.get("notification_topic_arn", "")),
        approval_topic_arn=str(aws_raw.get("approval_topic_arn", "")),
        state_machine_arn=str(aws_raw.get("state_machine_arn", "")),
        alb_target_group_arn=str(aws_raw.get("alb_target_group_arn", "")),
    )

    scaling = {
        level.key: _target_from(scaling_raw[level.key])
        for level in ScaleLevel
        if isinstance(scaling_raw.get(level.key), Mapping)
    }
    # config.example.yaml 互換: normal を level_0 として受け付ける
    if "level_0" not in scaling and isinstance(scaling_raw.get("normal"), Mapping):
        scaling["level_0"] = _target_from(scaling_raw["normal"])

    return Config(
        service_name=str(raw.get("service_name", "disaster-access-system")),
        region=str(raw.get("region", "ap-northeast-1")),
        dry_run=bool(raw.get("dry_run", True)),
        poll_interval_minutes=int(raw.get("poll_interval_minutes", 1)),
        jma=jma,
        safety=safety,
        aws=aws,
        scaling=scaling,
        severity_overrides=dict(raw.get("severity_overrides") or {}),
    )


def _parse_text(text: str, suffix: str) -> Mapping[str, Any]:
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(
                "YAML設定を読むには PyYAML が必要です。JSON設定を使うか層に同梱してください。"
            ) from exc
        return yaml.safe_load(text) or {}
    return json.loads(text)


def load(path: str | os.PathLike[str] | None = None) -> Config:
    """設定を読み込んで検証する。

    優先順位:
      1. 引数 path
      2. 環境変数 CONFIG_JSON (インラインJSON。Lambda環境変数向け)
      3. 環境変数 CONFIG_PATH
      4. ./config/config.yaml
    """
    if path is None:
        inline = os.environ.get("CONFIG_JSON")
        if inline:
            cfg = from_mapping(json.loads(inline))
            cfg = _apply_env_overrides(cfg)
            cfg.validate()
            return cfg
        path = os.environ.get("CONFIG_PATH") or "config/config.yaml"

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"設定ファイルが見つかりません: {p}")
    cfg = from_mapping(_parse_text(p.read_text(encoding="utf-8"), p.suffix.lower()))
    cfg = _apply_env_overrides(cfg)
    cfg.validate()
    return cfg


def _apply_env_overrides(cfg: Config) -> Config:
    """運用中に触る可能性が高い項目だけ環境変数で上書きできるようにする。

    DRY_RUN は「true にする方向」だけでなく明示的に false も許すが、
    未設定時は設定ファイルの値を尊重する(既定は true)。
    """
    import dataclasses

    dry_run_env = os.environ.get("DRY_RUN")
    changes: dict[str, Any] = {}
    if dry_run_env is not None:
        changes["dry_run"] = dry_run_env.strip().lower() in ("1", "true", "yes", "on")
    if os.environ.get("STATE_TABLE"):
        changes["aws"] = dataclasses.replace(
            cfg.aws, state_table=os.environ["STATE_TABLE"]
        )
    if os.environ.get("STATE_MACHINE_ARN"):
        base = changes.get("aws", cfg.aws)
        changes["aws"] = dataclasses.replace(
            base, state_machine_arn=os.environ["STATE_MACHINE_ARN"]
        )
    if os.environ.get("NOTIFICATION_TOPIC_ARN"):
        base = changes.get("aws", cfg.aws)
        changes["aws"] = dataclasses.replace(
            base, notification_topic_arn=os.environ["NOTIFICATION_TOPIC_ARN"]
        )
    return dataclasses.replace(cfg, **changes) if changes else cfg
