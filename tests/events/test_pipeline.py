"""Event enrichment pipeline integration tests (phase 5 Task 7)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tradingagents.events.contracts import (
    AnnouncementDateSource,
    EventSentiment,
    EventSeverity,
    EventSymbolLink,
    EventType,
    MarketEvent,
)
from tradingagents.market_data.contracts import PITLevel
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_screen
from tradingagents.screener.report import ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

FIXTURE = Path("tests/fixtures/screener/mvp_market.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _relaxed_config(**event_updates) -> ScreenerConfig:
    base = ScreenerConfig()
    relaxed = base.model_copy(update={
        "universe": base.universe.model_copy(update={
            "min_listing_days": 2,
            "min_avg_amount_20d": 1_000_000,
        }),
    })
    if event_updates:
        relaxed = relaxed.model_copy(update={
            "event_enrichment": relaxed.event_enrichment.model_copy(update=event_updates),
        })
    return relaxed


def _signal_time(fixture: dict) -> datetime:
    trading_dates = sorted(fixture["bars"])
    signal_date = trading_dates[-2]
    return datetime.combine(
        datetime.fromisoformat(signal_date).date(),
        datetime.min.time().replace(hour=15, minute=30),
        tzinfo=SHANGHAI,
    )


def _event(
    *,
    event_id: str,
    event_type: EventType,
    sentiment: EventSentiment,
    available_at: datetime,
    severity: EventSeverity = EventSeverity.MEDIUM,
    pit_level: PITLevel = PITLevel.PIT_REQUIRED,
) -> MarketEvent:
    return MarketEvent(
        event_id=event_id,
        event_type=event_type,
        title=f"title-{event_id}",
        published_at=available_at,
        available_at=available_at,
        source="fixture",
        source_url="https://example.com/event",
        source_record_id=event_id,
        content_hash=f"hash-{event_id}",
        pit_level=pit_level,
        sentiment=sentiment,
        severity=severity,
        announcement_date_source=AnnouncementDateSource.REPORTED,
    )


def _publish_events(repo, bundles: list[tuple[MarketEvent, str, list[dict]]]) -> None:
    run_id = repo.begin_ingestion_run("market_events", {"source": "test"})
    events: list[MarketEvent] = []
    links: list[EventSymbolLink] = []
    tags: list[dict] = []
    for event, symbol, event_tags in bundles:
        events.append(event)
        links.append(EventSymbolLink(
            event_id=event.event_id,
            symbol=symbol,
            role="primary",
            available_at=event.available_at or event.published_at,
            source=event.source,
        ))
        tags.extend(event_tags)
    repo.upsert_staging_event_bundle(run_id, events=events, links=links, tags=tags)
    repo.publish_event_bundle(run_id)


def _seed_candidate_events(repo) -> None:
    available = datetime(2025, 12, 10, 9, 30, tzinfo=SHANGHAI)
    _publish_events(repo, [
        (
            _event(
                event_id="evt-600001",
                event_type=EventType.BUYBACK,
                sentiment=EventSentiment.POSITIVE,
                available_at=available,
                severity=EventSeverity.HIGH,
            ),
            "600001",
            [],
        ),
        (
            _event(
                event_id="evt-600002",
                event_type=EventType.PENALTY,
                sentiment=EventSentiment.NEGATIVE,
                available_at=available,
                severity=EventSeverity.HIGH,
            ),
            "600002",
            [{"event_id": "evt-600002", "tag_key": "soft_risk", "tag_value": "penalty:medium"}],
        ),
        (
            _event(
                event_id="evt-600003",
                event_type=EventType.MANAGEMENT_CHANGE,
                sentiment=EventSentiment.NEUTRAL,
                available_at=available,
                severity=EventSeverity.LOW,
            ),
            "600003",
            [],
        ),
    ])


def test_disabled_enrichment_preserves_stage4_ranking_and_portfolio(tmp_path: Path):
    fixture = _load_fixture()
    config = _relaxed_config(enabled=False)
    baseline = run_screen(fixture, config, tmp_path / "base.duckdb")
    with_meta = run_screen(
        fixture,
        _relaxed_config(enabled=False, candidate_limit=50),
        tmp_path / "meta.duckdb",
    )
    assert baseline.ranking == with_meta.ranking
    assert baseline.target_weights == with_meta.target_weights
    assert baseline.base_ranking == []
    assert baseline.enhanced_ranking == []


def test_enabled_enrichment_exposes_three_rankings_without_overwriting_base(tmp_path: Path):
    fixture = _load_fixture()
    config = _relaxed_config(enabled=True, candidate_limit=3)
    db_path = tmp_path / "enabled.duckdb"
    run_screen(
        fixture,
        config,
        db_path,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    _seed_candidate_events(MarketDataRepository(db_path))
    report = run_screen(
        fixture,
        config,
        db_path,
        reload=False,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    assert report.status == ScreeningStatus.OK
    assert report.base_ranking == report.ranking
    assert report.event_ranking
    assert report.enhanced_ranking
    assert report.event_contributions
    assert "600001" in report.event_contributions


def test_repository_queries_only_top_candidates(tmp_path: Path):
    fixture = _load_fixture()
    config = _relaxed_config(enabled=True, candidate_limit=2)
    db_path = tmp_path / "limited.duckdb"
    report = run_screen(
        fixture,
        config,
        db_path,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    repo = MarketDataRepository(db_path)
    _seed_candidate_events(repo)
    queried: list[list[str]] = []

    original = repo.get_market_events

    def _tracked(symbols, available_before):
        queried.append(list(symbols))
        return original(symbols, available_before)

    with patch.object(repo, "get_market_events", side_effect=_tracked):
        from tradingagents.screener.event_enrichment import enrich_ranking_with_events

        enrich_ranking_with_events(
            repo,
            config,
            base_ranking=report.ranking,
            base_scores={symbol: 1.0 for symbol in report.ranking},
            signal_time=report.signal_time,
        )
    assert queried
    assert len(queried[0]) == 2
    assert set(queried[0]).issubset(set(report.ranking[:2]))


def test_future_events_do_not_affect_historical_enrichment(tmp_path: Path):
    fixture = _load_fixture()
    config = _relaxed_config(enabled=True, candidate_limit=3)
    db_path = tmp_path / "pit.duckdb"
    baseline = run_screen(
        fixture,
        config,
        db_path,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    repo = MarketDataRepository(db_path)
    future_available = datetime(2026, 1, 10, 9, 30, tzinfo=SHANGHAI)
    _publish_events(repo, [
        (
            _event(
                event_id="evt-future",
                event_type=EventType.EARNINGS_FORECAST,
                sentiment=EventSentiment.POSITIVE,
                available_at=future_available,
            ),
            "600001",
            [],
        ),
    ])
    after = run_screen(
        fixture,
        config,
        db_path,
        reload=False,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    assert baseline.ranking == after.ranking
    assert baseline.target_weights == after.target_weights
    assert "evt-future" not in {
        item["event_id"]
        for items in after.event_contributions.values()
        for item in items
    }


def test_hard_risk_flags_exclude_symbol_from_enhanced_ranking(tmp_path: Path):
    fixture = _load_fixture()
    config = _relaxed_config(enabled=True, candidate_limit=3, hard_risk_filter=True)
    db_path = tmp_path / "hard.duckdb"
    run_screen(
        fixture,
        config,
        db_path,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    repo = MarketDataRepository(db_path)
    available = datetime(2025, 12, 10, 9, 30, tzinfo=SHANGHAI)
    _publish_events(repo, [
        (
            _event(
                event_id="evt-delist",
                event_type=EventType.ST_DELIST,
                sentiment=EventSentiment.NEGATIVE,
                available_at=available,
                severity=EventSeverity.CRITICAL,
            ),
            "600002",
            [],
        ),
    ])
    report = run_screen(
        fixture,
        config,
        db_path,
        reload=False,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    assert "600002" in report.risk_flags
    assert report.enhanced_ranking[0] != "600002" or report.risk_flags["600002"]


def test_required_announcements_missing_records_enrichment_error(tmp_path: Path):
    fixture = _load_fixture()
    config = _relaxed_config(enabled=True, candidate_limit=3, require_announcements=True)
    report = run_screen(
        fixture,
        config,
        tmp_path / "required.duckdb",
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    assert report.status == ScreeningStatus.OK
    assert report.ranking
    assert "official_announcements missing" in " ".join(report.event_enrichment_errors)
    assert report.enhanced_ranking == report.ranking


def test_contributions_are_reversible_to_enhanced_ordering(tmp_path: Path):
    fixture = _load_fixture()
    config = _relaxed_config(enabled=True, candidate_limit=3)
    db_path = tmp_path / "recompute.duckdb"
    run_screen(
        fixture,
        config,
        db_path,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    repo = MarketDataRepository(db_path)
    _seed_candidate_events(repo)
    report = run_screen(
        fixture,
        config,
        db_path,
        reload=False,
        universe_request=UniverseRequest(
            universe_type=UniverseType.ALL,
            as_of=_signal_time(fixture),
        ),
    )
    assert report.enhanced_ranking
    assert report.event_contributions
