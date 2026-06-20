"""Normalize free-path provider rows into event contracts."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlparse

from tradingagents.events.contracts import (
    AnnouncementDateSource,
    EventSentiment,
    EventSeverity,
    EventSymbolLink,
    EventType,
    MarketEvent,
)
from tradingagents.market_data.contracts import PITLevel
from tradingagents.market_data.financials import next_open_trading_day
from tradingagents.market_data.market_hours import SHANGHAI, ensure_aware_shanghai

_SYMBOL_RE = re.compile(r"(\d{6})")
_ALLOWED_URL_HOSTS = frozenset({
    "vip.stock.finance.sina.com.cn",
    "finance.sina.com.cn",
    "money.finance.sina.com.cn",
    "quotes.sina.cn",
    "search-api-web.eastmoney.com",
    "so.eastmoney.com",
    "push2.eastmoney.com",
    "push2his.eastmoney.com",
    "zx.10jqka.com.cn",
})

_TYPE_RULES: tuple[tuple[re.Pattern[str], EventType], ...] = (
    (re.compile(r"年度报告|半年度报告|季度报告"), EventType.FINANCIAL_REPORT),
    (re.compile(r"业绩预告|业绩快报"), EventType.EARNINGS_FORECAST),
    (re.compile(r"分红|派息"), EventType.DIVIDEND),
    (re.compile(r"回购"), EventType.BUYBACK),
    (re.compile(r"增持|减持"), EventType.HOLDING_CHANGE),
    (re.compile(r"质押"), EventType.PLEDGE),
    (re.compile(r"重大合同|中标"), EventType.MAJOR_CONTRACT),
    (re.compile(r"重组|收购"), EventType.RESTRUCTURING),
    (re.compile(r"立案|调查"), EventType.INVESTIGATION),
    (re.compile(r"处罚|警示函"), EventType.PENALTY),
    (re.compile(r"ST|退市"), EventType.ST_DELIST),
    (re.compile(r"停牌|复牌"), EventType.SUSPEND_RESUME),
    (re.compile(r"解禁"), EventType.LOCKUP),
    (re.compile(r"董事|高管|管理层"), EventType.MANAGEMENT_CHANGE),
)

_POSITIVE_HINTS = re.compile(r"增持|中标|回购|分红|利好|预增|增长")
_NEGATIVE_HINTS = re.compile(r"减持|处罚|立案|亏损|预减|警示|违规|退市")


def normalize_symbol(symbol: str) -> str:
    text = symbol.strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    match = _SYMBOL_RE.search(text)
    if match is None:
        raise ValueError(f"invalid A-share symbol: {symbol!r}")
    return match.group(1)


def sanitize_event_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid event url: {url!r}")
    host = parsed.hostname or ""
    if host not in _ALLOWED_URL_HOSTS:
        raise ValueError(f"event url host not allowed: {host}")
    return url.strip()


def classify_event_type(title: str) -> EventType:
    for pattern, event_type in _TYPE_RULES:
        if pattern.search(title):
            return event_type
    return EventType.NEWS


def infer_sentiment(title: str) -> EventSentiment:
    if _NEGATIVE_HINTS.search(title):
        return EventSentiment.NEGATIVE
    if _POSITIVE_HINTS.search(title):
        return EventSentiment.POSITIVE
    return EventSentiment.UNKNOWN


def conservative_available_at(
    published_date: date,
    *,
    open_dates: list[date] | None = None,
) -> datetime:
    next_open = next_open_trading_day(published_date, open_dates)
    return datetime.combine(next_open, time(9, 30), tzinfo=SHANGHAI)


def content_hash_for(title: str, source_record_id: str, source_url: str) -> str:
    payload = f"{title}|{source_record_id}|{source_url}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def infer_severity(title: str, event_type: EventType) -> EventSeverity:
    if event_type in {EventType.ST_DELIST, EventType.INVESTIGATION}:
        return EventSeverity.CRITICAL
    if event_type == EventType.PENALTY and re.search(r"重大|严重", title):
        return EventSeverity.CRITICAL
    if event_type == EventType.SUSPEND_RESUME and re.search(r"长期停牌", title):
        return EventSeverity.CRITICAL
    if event_type in {
        EventType.PENALTY,
        EventType.INVESTIGATION,
        EventType.ST_DELIST,
        EventType.SUSPEND_RESUME,
    }:
        return EventSeverity.HIGH
    return EventSeverity.MEDIUM


def normalize_announcement_row(
    row: dict[str, Any],
    *,
    open_dates: list[date] | None = None,
    source: str = "free_astock",
    ingested_at: datetime | None = None,
) -> tuple[MarketEvent, EventSymbolLink]:
    symbol = normalize_symbol(str(row["symbol"]))
    title = str(row["title"]).strip()
    published_date = row.get("published_date")
    if isinstance(published_date, str):
        published_date = date.fromisoformat(published_date[:10])
    if not isinstance(published_date, date):
        raise ValueError("announcement row requires published_date")
    published_at = datetime.combine(published_date, time(16, 0), tzinfo=SHANGHAI)
    source_url = sanitize_event_url(str(row.get("source_url") or ""))
    source_record_id = str(row.get("source_record_id") or "").strip()
    if not source_record_id:
        raise ValueError("announcement row requires source_record_id")
    source_version = str(row.get("source_version") or "v1")
    available_at = conservative_available_at(published_date, open_dates=open_dates)
    event_id = str(row.get("event_id") or uuid.uuid4())
    event_type = classify_event_type(title)
    event = MarketEvent(
        event_id=event_id,
        event_type=event_type,
        title=title,
        summary=str(row.get("summary") or title)[:500],
        published_at=published_at,
        available_at=available_at,
        source=source,
        source_url=source_url,
        source_record_id=source_record_id,
        source_version=source_version,
        content_hash=content_hash_for(title, source_record_id, source_url),
        pit_level=PITLevel.PIT_REQUIRED,
        sentiment=infer_sentiment(title),
        severity=infer_severity(title, event_type),
        announcement_date_source=AnnouncementDateSource.REPORTED,
        supersedes_event_id=row.get("supersedes_event_id"),
        ingested_at=ensure_aware_shanghai(ingested_at or datetime.now(tz=SHANGHAI)),
    )
    link = EventSymbolLink(
        event_id=event.event_id,
        symbol=symbol,
        role="primary",
        available_at=available_at,
        source=source,
    )
    return event, link


def normalize_news_row(
    row: dict[str, Any],
    *,
    source: str = "free_astock",
    ingested_at: datetime | None = None,
) -> tuple[MarketEvent, EventSymbolLink]:
    symbol = normalize_symbol(str(row["symbol"]))
    title = str(row["title"]).strip()
    published_at = row.get("published_at")
    if isinstance(published_at, str):
        published_at = ensure_aware_shanghai(datetime.fromisoformat(published_at))
    elif isinstance(published_at, datetime):
        published_at = ensure_aware_shanghai(published_at)
    else:
        raise ValueError("news row requires published_at")
    source_url = sanitize_event_url(str(row.get("source_url") or row.get("url") or ""))
    source_record_id = str(row.get("source_record_id") or source_url)
    event_id = str(row.get("event_id") or uuid.uuid4())
    event = MarketEvent(
        event_id=event_id,
        event_type=EventType.NEWS,
        title=title,
        summary=title[:200],
        published_at=published_at,
        available_at=published_at,
        source=source,
        source_url=source_url,
        source_record_id=source_record_id,
        source_version=str(row.get("source_version") or "v1"),
        content_hash=content_hash_for(title, source_record_id, source_url),
        pit_level=PITLevel.BEST_EFFORT,
        sentiment=infer_sentiment(title),
        severity=EventSeverity.LOW,
        ingested_at=ensure_aware_shanghai(ingested_at or datetime.now(tz=SHANGHAI)),
    )
    link = EventSymbolLink(
        event_id=event.event_id,
        symbol=symbol,
        role="primary",
        available_at=published_at,
        source=source,
    )
    return event, link


def normalize_fund_flow_row(
    row: dict[str, Any],
    *,
    source: str = "free_astock",
    ingested_at: datetime | None = None,
) -> tuple[MarketEvent, EventSymbolLink]:
    symbol = normalize_symbol(str(row["symbol"]))
    title = str(row["title"]).strip()
    published_at = row.get("published_at")
    if isinstance(published_at, str):
        published_at = ensure_aware_shanghai(datetime.fromisoformat(published_at))
    elif isinstance(published_at, datetime):
        published_at = ensure_aware_shanghai(published_at)
    else:
        raise ValueError("fund flow row requires published_at")
    source_url = sanitize_event_url(str(row.get("source_url") or ""))
    source_record_id = str(row.get("source_record_id") or f"{symbol}:{published_at.date()}")
    sentiment = EventSentiment(str(row.get("sentiment", "unknown")))
    event_id = str(row.get("event_id") or uuid.uuid4())
    event = MarketEvent(
        event_id=event_id,
        event_type=EventType.FUND_FLOW,
        title=title,
        summary=title[:200],
        published_at=published_at,
        available_at=published_at,
        source=source,
        source_url=source_url,
        source_record_id=source_record_id,
        source_version=str(row.get("source_version") or "v1"),
        content_hash=content_hash_for(title, source_record_id, source_url),
        pit_level=PITLevel.BEST_EFFORT,
        sentiment=sentiment,
        severity=EventSeverity.LOW,
        ingested_at=ensure_aware_shanghai(ingested_at or datetime.now(tz=SHANGHAI)),
    )
    link = EventSymbolLink(
        event_id=event.event_id,
        symbol=symbol,
        role="primary",
        available_at=published_at,
        source=source,
    )
    return event, link


def normalize_hot_topic_row(
    row: dict[str, Any],
    *,
    source: str = "free_astock",
    ingested_at: datetime | None = None,
) -> MarketEvent:
    title = str(row["title"]).strip()
    published_at = row.get("published_at")
    if isinstance(published_at, str):
        published_at = ensure_aware_shanghai(datetime.fromisoformat(published_at))
    elif isinstance(published_at, datetime):
        published_at = ensure_aware_shanghai(published_at)
    else:
        raise ValueError("hot topic row requires published_at")
    source_url = sanitize_event_url(str(row.get("source_url") or ""))
    source_record_id = str(row.get("source_record_id") or title)
    event_id = str(row.get("event_id") or uuid.uuid4())
    return MarketEvent(
        event_id=event_id,
        event_type=EventType.HOT_TOPIC,
        title=title,
        summary=title[:200],
        published_at=published_at,
        available_at=published_at,
        source=source,
        source_url=source_url,
        source_record_id=source_record_id,
        source_version=str(row.get("source_version") or "v1"),
        content_hash=content_hash_for(title, source_record_id, source_url),
        pit_level=PITLevel.CURRENT_ONLY,
        sentiment=EventSentiment.UNKNOWN,
        severity=EventSeverity.LOW,
        ingested_at=ensure_aware_shanghai(ingested_at or datetime.now(tz=SHANGHAI)),
    )
