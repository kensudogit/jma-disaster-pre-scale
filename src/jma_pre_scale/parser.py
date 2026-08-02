"""気象庁防災情報XMLの解析と検証。

SKILL.md Phase 2「XMLサイズ上限、Content-Type、スキーマ、必須項目を検証する」
「訂正、取消、解除、続報を識別する」に対応する。

方針:
  - 名前空間は電文種別(気象/地震火山)で異なるため、ローカル名で走査する。
    (jmaxml1/ , informationBasis1/ , body/meteorology1/ , body/seismology1/ ...)
  - 未知の要素・欠損は例外にせず、判定不能として落とす。「未検証XMLをそのまま
    インフラ操作へ接続しない」ため、必須項目を満たさない電文は破棄する。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from .models import DisasterEvent, InfoType, Severity

_NS_STRIP = re.compile(r"^\{[^}]*\}")

#  XML爆弾対策: 展開エンティティを禁止する簡易チェック
_FORBIDDEN = re.compile(rb"<!(DOCTYPE|ENTITY)", re.IGNORECASE)


class XmlValidationError(ValueError):
    """電文が検証を通らなかった。呼び出し側は必ず HOLD 側に倒すこと。"""


@dataclass(frozen=True)
class FeedEntry:
    """Atom フィードの1エントリ。"""

    entry_id: str
    title: str
    updated: str
    link: str
    author: str = ""
    content: str = ""


def local(tag: str) -> str:
    return _NS_STRIP.sub("", tag)


def _iter(elem: ET.Element, name: str) -> Iterator[ET.Element]:
    for child in elem.iter():
        if local(child.tag) == name:
            yield child


def _first_text(elem: ET.Element | None, name: str, default: str = "") -> str:
    if elem is None:
        return default
    for found in _iter(elem, name):
        return (found.text or "").strip()
    return default


def _direct_children(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in elem if local(c.tag) == name]


# ---------------------------------------------------------------- Atom feed


def parse_feed(data: bytes) -> list[FeedEntry]:
    """Atom フィードをパースし、新しい順のエントリ一覧を返す。"""
    _assert_safe(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise XmlValidationError(f"Atomフィードのパースに失敗しました: {exc}") from exc
    if local(root.tag) != "feed":
        raise XmlValidationError(f"ルート要素がfeedではありません: {local(root.tag)}")

    entries: list[FeedEntry] = []
    for entry in _direct_children(root, "entry"):
        link = ""
        for link_el in _direct_children(entry, "link"):
            href = link_el.attrib.get("href", "")
            if href:
                link = href
                break
        entry_id = _first_text(entry, "id") or link
        if not entry_id or not link:
            continue  # 必須項目欠損は破棄
        entries.append(
            FeedEntry(
                entry_id=entry_id,
                title=_first_text(entry, "title"),
                updated=_first_text(entry, "updated"),
                link=link,
                author=_first_text(entry, "author"),
                content=_first_text(entry, "content"),
            )
        )
    return entries


# ------------------------------------------------------------- JMAXML 電文

#  Kind/Name の接尾辞から重大度を決める。コード表の改訂に強い。
_SEVERITY_BY_SUFFIX: tuple[tuple[str, Severity], ...] = (
    ("特別警報", Severity.EMERGENCY_WARNING),
    ("警報", Severity.WARNING),
    ("注意報", Severity.ADVISORY),
)

#  最大震度 -> 重大度
_SEVERITY_BY_INTENSITY: dict[str, Severity] = {
    "1": Severity.NONE, "2": Severity.NONE, "3": Severity.NONE,
    "4": Severity.ADVISORY,
    "5-": Severity.WARNING, "5+": Severity.WARNING,
    "6-": Severity.EMERGENCY_WARNING, "6+": Severity.EMERGENCY_WARNING,
    "7": Severity.EMERGENCY_WARNING,
}

_SEVERITY_BY_TSUNAMI_KIND: dict[str, Severity] = {
    "大津波警報": Severity.EMERGENCY_WARNING,
    "津波警報": Severity.EMERGENCY_WARNING,
    "津波注意報": Severity.WARNING,
    "津波予報": Severity.ADVISORY,
}

#  Kind/Name -> 災害種別
_EVENT_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("大雨", "heavy_rain"),
    ("洪水", "flood"),
    ("暴風雪", "storm"),
    ("暴風", "storm"),
    ("強風", "storm"),
    ("大雪", "snow"),
    ("風雪", "snow"),
    ("高潮", "high_tide"),
    ("波浪", "high_tide"),
    ("土砂災害", "landslide"),
    ("竜巻", "storm"),
    ("雷", "storm"),
)

#  解除・警報終了を意味する Kind/Status
_CANCEL_STATUSES = {"解除", "なし", "発表警報・注意報はなし"}


def _assert_safe(data: bytes) -> None:
    if _FORBIDDEN.search(data[:4096]):
        raise XmlValidationError("DOCTYPE/ENTITY を含む電文は受け付けません")


def _classify_weather(kind_name: str) -> tuple[str, Severity]:
    event_type = "other"
    for keyword, mapped in _EVENT_TYPE_KEYWORDS:
        if keyword in kind_name:
            event_type = mapped
            break
    severity = Severity.NONE
    for suffix, mapped_sev in _SEVERITY_BY_SUFFIX:
        if kind_name.endswith(suffix):
            severity = mapped_sev
            break
    return event_type, severity


def parse_report(
    data: bytes,
    *,
    source_url: str = "",
    max_bytes: int = 4 * 1024 * 1024,
) -> list[DisasterEvent]:
    """1電文を解析し、地域単位の DisasterEvent 群に展開する。

    地域ごとに1件出すので、対象地域フィルタは呼び出し側(rules)で行える。
    """
    if len(data) > max_bytes:
        raise XmlValidationError(f"電文サイズが上限を超えています: {len(data)} > {max_bytes}")
    _assert_safe(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise XmlValidationError(f"電文のパースに失敗しました: {exc}") from exc
    if local(root.tag) != "Report":
        raise XmlValidationError(f"ルート要素がReportではありません: {local(root.tag)}")

    control = next(iter(_direct_children(root, "Control")), None)
    head = next(iter(_direct_children(root, "Head")), None)
    body = next(iter(_direct_children(root, "Body")), None)
    if control is None or head is None or body is None:
        raise XmlValidationError("Control / Head / Body のいずれかが欠落しています")

    status = _first_text(control, "Status", "通常")
    title = _first_text(head, "Title") or _first_text(control, "Title")
    report_dt = _first_text(head, "ReportDateTime")
    jma_event_id = _first_text(head, "EventID")
    serial = _first_text(head, "Serial")
    info_kind = _first_text(head, "InfoKind")
    raw_info_type = _first_text(head, "InfoType", "発表")
    try:
        info_type = InfoType(raw_info_type)
    except ValueError:
        info_type = InfoType.ANNOUNCE

    if not report_dt:
        raise XmlValidationError("Head/ReportDateTime が欠落しています")

    #  電文IDは Head/EventID + Serial + ReportDateTime で一意化する。
    #  EventID が無い電文(定時気象情報など)は source_url を使う。
    base_id = jma_event_id or source_url or title
    event_id = f"{base_id}|{serial}|{report_dt}|{info_type.value}"

    cancelled_report = info_type is InfoType.CANCEL

    def build(event_type: str, area_code: str, area_name: str,
              severity: Severity, cancelled: bool) -> DisasterEvent:
        return DisasterEvent(
            event_id=f"{event_id}|{area_code or area_name}|{event_type}",
            event_type=event_type,
            area_code=area_code,
            area_name=area_name,
            severity=severity,
            report_datetime=report_dt,
            title=f"{title} / {area_name}" if area_name else title,
            info_type=info_type,
            jma_event_id=jma_event_id,
            serial=serial,
            status=status,
            is_cancelled=cancelled or cancelled_report,
            source_url=source_url,
        )

    events: list[DisasterEvent] = []
    events.extend(_parse_warnings(body, build))
    events.extend(_parse_tsunami(body, build))
    events.extend(_parse_earthquake(body, build, title, info_kind))
    return events


def _parse_warnings(body: ET.Element, build) -> list[DisasterEvent]:
    """気象警報・注意報(VPWW5x)。Warning/Item/{Kind*, Area}。"""
    out: list[DisasterEvent] = []
    for warning in _iter(body, "Warning"):
        for item in _direct_children(warning, "Item"):
            area = next(iter(_direct_children(item, "Area")), None)
            area_code = _first_text(area, "Code")
            area_name = _first_text(area, "Name")
            for kind in _direct_children(item, "Kind"):
                kind_name = _first_text(kind, "Name")
                if not kind_name:
                    continue
                kind_status = _first_text(kind, "Status")
                event_type, severity = _classify_weather(kind_name)
                if severity is Severity.NONE:
                    continue
                out.append(
                    build(event_type, area_code, area_name, severity,
                          kind_status in _CANCEL_STATUSES)
                )
    return out


def _parse_tsunami(body: ET.Element, build) -> list[DisasterEvent]:
    """津波警報・注意報・予報(VTSE41等)。Tsunami/Forecast/Item/{Category,Area}。"""
    out: list[DisasterEvent] = []
    for tsunami in _iter(body, "Tsunami"):
        for forecast in _iter(tsunami, "Forecast"):
            for item in _direct_children(forecast, "Item"):
                area = next(iter(_direct_children(item, "Area")), None)
                area_code = _first_text(area, "Code")
                area_name = _first_text(area, "Name")
                category = next(iter(_direct_children(item, "Category")), None)
                kind_name = _first_text(category, "Name") if category is not None else ""
                if not kind_name:
                    continue
                severity = _SEVERITY_BY_TSUNAMI_KIND.get(kind_name, Severity.NONE)
                cancelled = "解除" in kind_name or kind_name == "津波なし"
                if severity is Severity.NONE and not cancelled:
                    continue
                out.append(
                    build("tsunami", area_code, area_name,
                          severity if not cancelled else Severity.NONE, cancelled)
                )
    return out


def _parse_earthquake(body: ET.Element, build, title: str, info_kind: str) -> list[DisasterEvent]:
    """震度速報/震源・震度情報(VXSE51/53)。Intensity/Observation/Pref/MaxInt。"""
    if "震度" not in title and "震度" not in info_kind and "地震" not in title:
        return []
    out: list[DisasterEvent] = []
    seen: set[tuple[str, str]] = set()
    for intensity in _iter(body, "Intensity"):
        for observation in _iter(intensity, "Observation"):
            prefs = list(_iter(observation, "Pref"))
            if not prefs:
                max_int = _first_text(observation, "MaxInt")
                severity = _SEVERITY_BY_INTENSITY.get(max_int, Severity.NONE)
                if severity is not Severity.NONE:
                    out.append(build("earthquake", "", "全国", severity, False))
                continue
            for pref in prefs:
                area_code = _first_text(pref, "Code")
                area_name = _first_text(pref, "Name")
                max_int = _first_text(pref, "MaxInt")
                severity = _SEVERITY_BY_INTENSITY.get(max_int, Severity.NONE)
                if severity is Severity.NONE:
                    continue
                key = (area_code, area_name)
                if key in seen:
                    continue
                seen.add(key)
                out.append(build("earthquake", area_code, area_name, severity, False))
    return out
