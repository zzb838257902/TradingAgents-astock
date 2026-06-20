"""Event repository and atomic publish tests (phase 5 Task 3)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.events.contracts import (
    AnnouncementDateSource,
    EventQualityStatus,
    EventSentiment,
    EventSeverity,
    EventSymbolLink,
    EventType,
    MarketEvent,
    stable_event_id,
)
from tradingagents.market_data.contracts import PITLevel
from tradingagents.market_data.migrations import CURRENT_SCHEMA_VERSION
from tradingagents.market_data.repository import MarketDataRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")


_UNSET = object()


def _event(
    *,
    event_id: str = "evt-1",
    source_record_id: str = "123",
    source_version: str = "v1",
    available_at: datetime | None | object = _UNSET,
    quality_status: EventQualityStatus = EventQualityStatus.VALID,
    supersedes_event_id: str | None = None,
) -> MarketEvent:
    published = datetime(2026, 4, 30, 16, 0, tzinfo=SHANGHAI)
    if available_at is _UNSET:
        available = datetime(2026, 5, 6, 9, 30, tzinfo=SHANGHAI)
    else:
        available = available_at
    return MarketEvent(
        event_id=event_id,
        event_type=EventType.FINANCIAL_REPORT,
        title="Annual report",
        summary="summary",
        published_at=published,
        available_at=available,
        source="free_astock",
        source_url="https://example.com/1",
        source_record_id=source_record_id,
        source_version=source_version,
        content_hash="hash-1",
        pit_level=PITLevel.PIT_REQUIRED,
        sentiment=EventSentiment.NEUTRAL,
        severity=EventSeverity.MEDIUM,
        announcement_date_source=AnnouncementDateSource.REPORTED,
        quality_status=quality_status,
        supersedes_event_id=supersedes_event_id,
        ingested_at=datetime(2026, 6, 20, 10, 0, tzinfo=SHANGHAI),
    )


def _link(event_id: str, symbol: str = "600000") -> EventSymbolLink:
    return EventSymbolLink(
        event_id=event_id,
        symbol=symbol,
        role="primary",
        available_at=datetime(2026, 5, 6, 9, 30, tzinfo=SHANGHAI),
        source="free_astock",
    )


def test_schema_version_includes_event_tables(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    assert CURRENT_SCHEMA_VERSION == 10
    tables = {row[0] for row in repo.connection.execute("SHOW TABLES").fetchall()}
    for name in (
        "market_events",
        "staging_market_events",
        "event_symbol_links",
        "staging_event_symbol_links",
        "event_tags",
        "staging_event_tags",
        "board_aliases",
    ):
        assert name in tables


def test_publish_event_bundle_is_atomic(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    run_id = repo.begin_ingestion_run("market_events", {"symbols": ["600000"]})
    event = _event()
    repo.upsert_staging_event_bundle(
        run_id,
        events=[event],
        links=[_link(event.event_id)],
        tags=[{"event_id": event.event_id, "tag_key": "category", "tag_value": "report"}],
    )
    version_id = repo.publish_event_bundle(run_id)
    published = repo.get_latest_published_version("market_events")
    assert published is not None
    assert published["version_id"] == version_id
    rows = repo.get_market_events(
        ["600000"],
        available_before=datetime(2026, 6, 20, 16, 0, tzinfo=SHANGHAI),
    )
    assert len(rows) == 1
    assert rows[0]["event_id"] == event.event_id
    tags = repo.connection.execute(
        "SELECT tag_key, tag_value FROM event_tags WHERE event_id = ?",
        [event.event_id],
    ).fetchall()
    assert tags == [("category", "report")]


def test_failed_event_bundle_publish_leaves_no_visible_rows(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    run_id = repo.begin_ingestion_run("market_events", {})
    bad = _event(
        event_id="evt-bad",
        source_record_id="999",
        available_at=None,
        quality_status=EventQualityStatus.VALID,
    )
    repo.upsert_staging_event_bundle(run_id, events=[bad], links=[], tags=[])
    with pytest.raises(ValueError, match="event bundle quality gate"):
        repo.publish_event_bundle(run_id)
    visible = repo.get_market_events(
        ["600000"],
        available_before=datetime(2026, 6, 20, 16, 0, tzinfo=SHANGHAI),
    )
    assert visible == []
    version = repo.connection.execute(
        "SELECT status FROM dataset_versions WHERE ingestion_run_id = ?",
        [run_id],
    ).fetchall()
    assert not version or version[0][0] != "PUBLISHED"


def test_revision_events_coexist_with_supersedes_link(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    run_id = repo.begin_ingestion_run("market_events", {})
    original = _event(event_id="evt-old", source_record_id="1", source_version="v1")
    revised = _event(
        event_id="evt-new",
        source_record_id="1",
        source_version="v2",
        supersedes_event_id=original.event_id,
        available_at=datetime(2026, 5, 7, 9, 30, tzinfo=SHANGHAI),
    )
    repo.upsert_staging_event_bundle(
        run_id,
        events=[original, revised],
        links=[_link(original.event_id), _link(revised.event_id)],
        tags=[],
    )
    repo.publish_event_bundle(run_id)
    rows = repo.get_market_events(
        ["600000"],
        available_before=datetime(2026, 6, 20, 16, 0, tzinfo=SHANGHAI),
    )
    assert {row["event_id"] for row in rows} == {"evt-old", "evt-new"}
    assert stable_event_id(original) != stable_event_id(revised)


def test_pit_query_excludes_future_and_rejected_events(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    run_id = repo.begin_ingestion_run("market_events", {})
    ok = _event(event_id="evt-ok", source_record_id="10")
    future = _event(
        event_id="evt-future",
        source_record_id="11",
        available_at=datetime(2026, 7, 1, 9, 30, tzinfo=SHANGHAI),
    )
    rejected = _event(
        event_id="evt-rej",
        source_record_id="12",
        quality_status=EventQualityStatus.REJECTED,
    )
    repo.upsert_staging_event_bundle(
        run_id,
        events=[ok, future, rejected],
        links=[_link("evt-ok"), _link("evt-future"), _link("evt-rej")],
        tags=[],
    )
    repo.publish_event_bundle(run_id)
    rows = repo.get_market_events(
        ["600000"],
        available_before=datetime(2026, 6, 20, 16, 0, tzinfo=SHANGHAI),
    )
    assert [row["event_id"] for row in rows] == ["evt-ok"]


def test_duplicate_stable_keys_rejected_in_staging(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    run_id = repo.begin_ingestion_run("market_events", {})
    first = _event(event_id="evt-a", source_record_id="dup", source_version="v1")
    second = _event(event_id="evt-b", source_record_id="dup", source_version="v1")
    repo.upsert_staging_event_bundle(run_id, events=[first], links=[], tags=[])
    with pytest.raises(ValueError, match="duplicate stable event key"):
        repo.upsert_staging_event_bundle(run_id, events=[second], links=[], tags=[])


def test_board_aliases_do_not_change_board_definitions(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_board_definitions([{
        "board_type": "concept",
        "board_code": "BK1184.DC",
        "name": "人工智能",
        "pit_level": "current_only",
        "source": "fixture",
        "available_at": datetime(2026, 1, 2, 9, 0, tzinfo=SHANGHAI),
    }])
    repo.upsert_board_aliases([{
        "board_type": "concept",
        "board_code": "BK1184.DC",
        "alias": "AI概念",
        "alias_normalized": "ai概念",
        "source": "fixture",
    }])
    definition = repo.get_board_definition("concept", "BK1184.DC")
    assert definition is not None
    assert definition["name"] == "人工智能"
    aliases = repo.lookup_board_aliases("ai概念")
    assert aliases[0]["board_code"] == "BK1184.DC"
