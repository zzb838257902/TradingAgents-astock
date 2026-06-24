"""Event normalizer tests (phase 5 Task 5)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.events.contracts import EventSentiment, EventSeverity, EventType
from tradingagents.events.normalizer import (
    classify_event_type,
    conservative_available_at,
    infer_sentiment,
    infer_severity,
    normalize_announcement_row,
    normalize_symbol,
    sanitize_event_url,
)
from tradingagents.market_data.contracts import PITLevel

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_normalize_symbol_accepts_exchange_suffix():
    assert normalize_symbol("600000.SH") == "600000"


def test_classify_and_sentiment_for_penalty_title():
    title = "关于收到行政处罚决定书"
    assert classify_event_type(title) == EventType.PENALTY
    assert infer_sentiment(title) == EventSentiment.NEGATIVE


def test_infer_severity_marks_st_and_investigation_critical():
    assert infer_severity("关于公司股票被实施退市风险警示", EventType.ST_DELIST) == EventSeverity.CRITICAL
    assert infer_severity("关于收到立案调查通知书", EventType.INVESTIGATION) == EventSeverity.CRITICAL
    assert infer_severity("关于收到重大行政处罚决定书", EventType.PENALTY) == EventSeverity.CRITICAL


def test_infer_severity_treats_relief_announcements_as_low():
    assert infer_severity("关于撤销退市风险警示的公告", EventType.ST_DELIST) == EventSeverity.LOW
    assert infer_severity("关于终止调查的公告", EventType.INVESTIGATION) == EventSeverity.LOW
    assert infer_severity("关于终止立案的公告", EventType.INVESTIGATION) == EventSeverity.LOW


def test_termination_of_listing_is_critical_hard_risk():
    from tradingagents.events.contracts import AnnouncementDateSource, MarketEvent
    from tradingagents.events.scoring import is_hard_risk_event
    from tradingagents.market_data.contracts import PITLevel

    title = "关于公司股票终止上市的公告"
    assert classify_event_type(title) == EventType.ST_DELIST
    assert infer_severity(title, EventType.ST_DELIST) == EventSeverity.CRITICAL
    event = MarketEvent(
        event_id="evt-delist",
        event_type=EventType.ST_DELIST,
        title=title,
        published_at=datetime(2026, 5, 6, 16, 0, tzinfo=SHANGHAI),
        available_at=datetime(2026, 5, 7, 9, 30, tzinfo=SHANGHAI),
        source="fixture",
        source_url="https://example.com/1",
        source_record_id="delist-1",
        content_hash="hash",
        pit_level=PITLevel.PIT_REQUIRED,
        sentiment=EventSentiment.NEGATIVE,
        severity=infer_severity(title, EventType.ST_DELIST),
        announcement_date_source=AnnouncementDateSource.REPORTED,
    )
    assert is_hard_risk_event(event)


def test_conservative_available_at_uses_next_open_day():
    available = conservative_available_at(
        date(2026, 6, 5),
        open_dates=[date(2026, 6, 5), date(2026, 6, 8)],
    )
    assert available == datetime(2026, 6, 8, 9, 30, tzinfo=SHANGHAI)


def test_normalize_announcement_row_sets_pit_required():
    event, link = normalize_announcement_row({
        "symbol": "600000",
        "title": "2025年年度报告",
        "published_date": "2026-06-05",
        "source_record_id": "900001",
        "source_url": "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?stockid=600000&id=900001",
    })
    assert event.pit_level == PITLevel.PIT_REQUIRED
    assert link.symbol == "600000"
    assert event.available_at is not None


def test_sanitize_event_url_rejects_unknown_host():
    with pytest.raises(ValueError, match="not allowed"):
        sanitize_event_url("https://evil.example.com/a")
