"""XML解析と検証。SKILL.md Phase 2 / Phase 6「XML正常受信」「訂正・取消・解除」。"""
from __future__ import annotations

import pytest

from conftest import fixture_bytes
from jma_pre_scale.models import InfoType, Severity
from jma_pre_scale.parser import XmlValidationError, parse_feed, parse_report


def test_feedを解析してエントリを取り出せる():
    entries = parse_feed(fixture_bytes("feed_extra.xml"))
    assert len(entries) == 2
    assert entries[0].link.endswith("VPWW54_130000.xml")
    assert entries[0].entry_id == entries[0].link


def test_気象警報を解析して地域単位のイベントになる():
    events = parse_report(fixture_bytes("warning_tokyo_warning.xml"),
                          source_url="https://example/1.xml")
    kinds = {(e.event_type, e.severity) for e in events}
    assert ("heavy_rain", Severity.WARNING) in kinds
    assert ("storm", Severity.ADVISORY) in kinds  # 雷注意報
    assert all(e.area_code == "130000" for e in events)
    assert all(e.area_name == "東京都" for e in events)
    assert all(not e.is_cancelled for e in events)


def test_特別警報はemergency_warningになる():
    events = parse_report(fixture_bytes("warning_tokyo_emergency.xml"))
    heavy = [e for e in events if e.event_type == "heavy_rain"]
    assert heavy and heavy[0].severity is Severity.EMERGENCY_WARNING


def test_解除電文はis_cancelledになる():
    events = parse_report(fixture_bytes("warning_tokyo_release.xml"))
    assert events and all(e.is_cancelled for e in events)


def test_取消電文はis_cancelledかつinfo_typeが取消():
    events = parse_report(fixture_bytes("warning_tokyo_cancel.xml"))
    assert events
    assert all(e.info_type is InfoType.CANCEL for e in events)
    assert all(e.is_cancelled for e in events)


def test_訂正電文はis_correctionになる():
    events = parse_report(fixture_bytes("warning_tokyo_correction.xml"))
    assert events and all(e.is_correction for e in events)
    assert all(not e.is_cancelled for e in events)


def test_訓練電文はis_drillになる():
    events = parse_report(fixture_bytes("warning_tokyo_drill.xml"))
    assert events and all(e.is_drill for e in events)


def test_震度速報を府県ごとに展開する():
    events = parse_report(fixture_bytes("earthquake_intensity.xml"))
    by_area = {e.area_name: e for e in events}
    assert by_area["東京都"].severity is Severity.EMERGENCY_WARNING  # 震度6弱
    assert by_area["千葉県"].severity is Severity.ADVISORY           # 震度4
    assert all(e.event_type == "earthquake" for e in events)


def test_津波警報はemergency_warningになる():
    events = parse_report(fixture_bytes("tsunami_warning.xml"))
    assert len(events) == 1
    assert events[0].event_type == "tsunami"
    assert events[0].severity is Severity.EMERGENCY_WARNING
    assert events[0].area_code == "101"


def test_同一電文の解析結果は決定的でevent_idが安定する():
    a = parse_report(fixture_bytes("warning_tokyo_warning.xml"), source_url="u")
    b = parse_report(fixture_bytes("warning_tokyo_warning.xml"), source_url="u")
    assert [e.event_id for e in a] == [e.event_id for e in b]


def test_壊れたXMLは検証エラーになる():
    with pytest.raises(XmlValidationError):
        parse_report(fixture_bytes("malformed.xml"))


def test_DOCTYPEやENTITYを含む電文は拒否する():
    with pytest.raises(XmlValidationError, match="DOCTYPE"):
        parse_report(fixture_bytes("xxe.xml"))


def test_サイズ上限を超える電文は拒否する():
    with pytest.raises(XmlValidationError, match="サイズ"):
        parse_report(fixture_bytes("warning_tokyo_warning.xml"), max_bytes=10)


def test_ルート要素が違う電文は拒否する():
    with pytest.raises(XmlValidationError, match="Report"):
        parse_report(b"<?xml version='1.0'?><NotReport/>")
