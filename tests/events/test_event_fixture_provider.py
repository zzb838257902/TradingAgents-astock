"""Offline FixtureProvider event fetch tests (phase 5 Task 4)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradingagents.events.contracts import EventSentiment, stable_event_id
from tradingagents.market_data.contracts import DataStatus, PITLevel
from tradingagents.market_data.providers.fixture import FixtureProvider

FIXTURE_PATH = Path("tests/fixtures/events/provider_events.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _load_fixture(**overrides) -> dict:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture.update(overrides)
    return fixture


def test_fixture_event_probe_is_offline_without_token():
    provider = FixtureProvider(_load_fixture())
    result = provider.probe_event_capabilities()
    assert result.status == DataStatus.OK
    datasets = {item.dataset for item in result.data or []}
    assert "official_announcements" in datasets
    assert "event_news" in datasets


@pytest.mark.parametrize(
    ("symbol", "sentiment"),
    [
        ("600000", EventSentiment.POSITIVE),
        ("600001", EventSentiment.NEGATIVE),
        ("600002", EventSentiment.NEUTRAL),
    ],
)
def test_fixture_announcements_cover_sentiment_polarity(symbol, sentiment):
    provider = FixtureProvider(_load_fixture())
    result = provider.fetch_announcements(
        [symbol],
        date(2026, 5, 1),
        date(2026, 5, 3),
    )
    assert result.status == DataStatus.OK
    assert len(result.data or []) == 1
    assert result.data[0].sentiment == sentiment


def test_fixture_announcements_include_revision_chain():
    provider = FixtureProvider(_load_fixture())
    result = provider.fetch_announcements(
        ["600003"],
        date(2026, 4, 1),
        date(2026, 4, 30),
    )
    assert result.status == DataStatus.OK
    event_ids = {item.event_id for item in result.data or []}
    assert event_ids == {"evt-old", "evt-new"}
    by_id = {item.event_id: item for item in result.data or []}
    assert by_id["evt-new"].supersedes_event_id == "evt-old"
    assert stable_event_id(by_id["evt-old"]) != stable_event_id(by_id["evt-new"])


def test_fixture_announcements_expose_duplicate_stable_keys():
    provider = FixtureProvider(_load_fixture())
    result = provider.fetch_announcements(
        ["600000"],
        date(2026, 5, 4),
        date(2026, 5, 10),
    )
    assert result.status == DataStatus.OK
    stable_keys = [stable_event_id(item) for item in result.data or []]
    assert len(stable_keys) == 2
    assert len(set(stable_keys)) == 1


def test_fixture_announcements_exclude_future_published_dates():
    provider = FixtureProvider(_load_fixture())
    result = provider.fetch_announcements(
        ["600004"],
        date(2026, 5, 1),
        date(2026, 6, 30),
    )
    assert result.status == DataStatus.SUCCESS_EMPTY


def test_fixture_announcements_support_multi_symbol_event():
    provider = FixtureProvider(_load_fixture())
    result = provider.fetch_announcements(
        ["600005"],
        date(2026, 5, 1),
        date(2026, 5, 31),
    )
    assert result.status == DataStatus.OK
    assert len(result.data or []) == 1
    assert result.data[0].event_id == "evt-multi"


def test_fixture_no_announcements_scenario_is_success_empty():
    provider = FixtureProvider(_load_fixture(active_scenario="no_announcements"))
    result = provider.fetch_announcements(
        ["600000"],
        date(2026, 5, 1),
        date(2026, 5, 31),
    )
    assert result.status == DataStatus.SUCCESS_EMPTY
    assert result.data == []


def test_fixture_network_error_does_not_become_success_empty():
    provider = FixtureProvider(_load_fixture(active_scenario="network_error"))
    result = provider.fetch_announcements(
        ["600000"],
        date(2026, 5, 1),
        date(2026, 5, 31),
    )
    assert result.status == DataStatus.NETWORK_ERROR
    assert result.data is None
    assert result.status != DataStatus.SUCCESS_EMPTY


def test_fixture_hot_topics_rejects_historical_trade_date(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.providers.fixture.live_snapshot_date_error",
        lambda requested, *, dataset: (
            f"{dataset} live snapshot cannot be synced for historical date {requested}"
        ),
    )
    provider = FixtureProvider(_load_fixture())
    result = provider.fetch_hot_topics(date(2026, 6, 19))
    assert result.status == DataStatus.ERROR
    assert result.pit_level == PITLevel.CURRENT_ONLY
    assert result.errors


def test_fixture_hot_topics_ok_for_same_day(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.providers.fixture.live_snapshot_date_error",
        lambda requested, *, dataset: None,
    )
    provider = FixtureProvider(_load_fixture())
    result = provider.fetch_hot_topics(date(2026, 6, 20))
    assert result.status == DataStatus.OK
    assert len(result.data or []) == 1
