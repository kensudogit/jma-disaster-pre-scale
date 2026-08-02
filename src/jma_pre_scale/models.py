"""ドメインモデル。

SKILL.md Phase 3 の判定結果、Phase 4 の適用対象をここで型として固定する。
外部システム(既存アプリケーション)には一切依存しない。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import IntEnum, Enum
from typing import Any, Mapping, Sequence


class ScaleLevel(IntEnum):
    """スケールレベル。SKILL.md Phase 3 の標準レベルに対応する。"""

    LEVEL_0 = 0  # 変更なし(平時容量)
    LEVEL_1 = 1  # 最小予備容量へ拡張
    LEVEL_2 = 2  # 通常時の数倍へ拡張
    LEVEL_3 = 3  # 最大想定容量へ拡張

    @property
    def key(self) -> str:
        return self.name.lower()


class SystemState(str, Enum):
    """references/architecture.md の状態遷移。

    NORMAL -> WATCH -> WARNING -> EMERGENCY -> COOLDOWN -> NORMAL
                      \\-> HOLD
    """

    NORMAL = "NORMAL"
    WATCH = "WATCH"
    WARNING = "WARNING"
    EMERGENCY = "EMERGENCY"
    HOLD = "HOLD"
    COOLDOWN = "COOLDOWN"

    @classmethod
    def for_level(cls, level: "ScaleLevel") -> "SystemState":
        return {
            ScaleLevel.LEVEL_0: cls.NORMAL,
            ScaleLevel.LEVEL_1: cls.WATCH,
            ScaleLevel.LEVEL_2: cls.WARNING,
            ScaleLevel.LEVEL_3: cls.EMERGENCY,
        }[level]


class Severity(str, Enum):
    """情報レベル。気象庁の電文種別を正規化したもの。"""

    NONE = "none"
    ADVISORY = "advisory"                  # 注意報 / 震度4 など
    WARNING = "warning"                    # 警報 / 震度5弱-5強 / 津波注意報
    EMERGENCY_WARNING = "emergency_warning"  # 特別警報 / 震度6弱以上 / 津波警報・大津波警報


class InfoType(str, Enum):
    """Head/InfoType。訂正・取消の識別に使う。"""

    ANNOUNCE = "発表"
    CORRECTION = "訂正"
    DELAY = "遅延"
    CANCEL = "取消"


class Action(str, Enum):
    """判定結果として制御基盤が取るべき行動。"""

    SCALE_OUT = "SCALE_OUT"    # 拡張する
    HOLD = "HOLD"              # 現在容量を維持する(縮小しない)
    SCALE_IN = "SCALE_IN"      # クールダウン満了後の段階縮小
    NOOP = "NOOP"              # 何もしない


@dataclass(frozen=True)
class DisasterEvent:
    """気象庁電文1通から抽出した、判定に必要な最小限の情報。"""

    event_id: str               # 重複排除キー(電文ID)
    event_type: str             # heavy_rain / flood / typhoon / earthquake / tsunami ...
    area_code: str              # 府県予報区等コード
    severity: Severity
    area_name: str = ""         # 地域名(コード体系差異の補助一致用)
    report_datetime: str = ""   # ISO8601
    title: str = ""
    info_type: InfoType = InfoType.ANNOUNCE
    jma_event_id: str = ""      # Head/EventID(続報の紐付け)
    serial: str = ""
    status: str = "通常"        # Control/Status: 通常/訓練/試験
    is_cancelled: bool = False  # 解除 or 取消
    source_url: str = ""

    @property
    def is_correction(self) -> bool:
        return self.info_type in (InfoType.CORRECTION, InfoType.DELAY)

    @property
    def is_drill(self) -> bool:
        """訓練・試験電文。絶対にインフラ操作へ接続しない。"""
        return self.status not in ("通常", "")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["info_type"] = self.info_type.value
        return d


@dataclass(frozen=True)
class ScalingTarget:
    """適用対象の容量。値は「絶対値」であり増分ではない(冪等性のため)。"""

    ecs_desired_count: int
    ecs_min_capacity: int
    aurora_min_acu: float
    aurora_max_acu: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    """Poller が下した判定。Step Functions へ渡す入力そのもの。"""

    action: Action
    level: ScaleLevel
    previous_level: ScaleLevel
    state: SystemState
    reason: str
    target: ScalingTarget | None = None
    events: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    requires_approval: bool = False
    #  承認待ちがタイムアウトした場合に自動適用する縮退目標(縮小ではなく部分拡張)
    fallback_target: ScalingTarget | None = None
    dry_run: bool = True
    decided_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "level": int(self.level),
            "level_name": self.level.name,
            "previous_level": int(self.previous_level),
            "state": self.state.value,
            "reason": self.reason,
            "target": self.target.to_dict() if self.target else None,
            "events": list(self.events),
            "requires_approval": self.requires_approval,
            "fallback_target": self.fallback_target.to_dict() if self.fallback_target else None,
            "dry_run": self.dry_run,
            "decided_at": self.decided_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
