"""Scheduler job tests (offline)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.providers.fixture import FixtureProvider
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync
from tradingagents.scheduler.jobs import run_after_close
from tradingagents.scheduler.state import JobStateStore
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.report import ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType
from tradingagents.market_data.market_hours import post_close_signal_time

FIXTURE = Path("tests/fixtures/market_data/provider_mini.json")


def _setup(tmp_path: Path) -> tuple[MarketDataSync, ScreenerConfig, MarketDataPaths, dict]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    paths = MarketDataPaths(home_dir=tmp_path)
    repo = MarketDataRepository(paths.live_db_path, snapshot_dir=paths.snapshot_dir)
    load_fixture_into_repository(repo, fixture)
    provider = FixtureProvider(fixture)
    sync = MarketDataSync(repo, provider, paths)
    sync.probe_capabilities()
    config = ScreenerConfig(home_dir=tmp_path).model_copy(update={
        "universe": ScreenerConfig().universe.model_copy(update={
            "min_listing_days": 1,
            "min_avg_amount_20d": 1_000_000,
        }),
    })
    return sync, config, paths, fixture


def test_after_close_job_is_idempotent(tmp_path):
    sync, config, paths, fixture = _setup(tmp_path)
    trade_date = date(2026, 1, 2)
    first = run_after_close(trade_date, config, paths, sync, fixture=fixture)
    second = run_after_close(trade_date, config, paths, sync, fixture=fixture)
    assert first.status == "success"
    assert second.skipped is True
    store = JobStateStore(paths.home_dir / "scheduler")
    assert len(store.load_run(first.job_key)["attempts"]) == 1


def test_after_close_force_rerun_creates_second_attempt(tmp_path):
    sync, config, paths, fixture = _setup(tmp_path)
    trade_date = date(2026, 1, 2)
    first = run_after_close(trade_date, config, paths, sync, fixture=fixture)
    run_after_close(trade_date, config, paths, sync, fixture=fixture, force=True)
    store = JobStateStore(paths.home_dir / "scheduler")
    assert len(store.load_run(first.job_key)["attempts"]) == 2


def test_after_close_fixture_skips_live_sync(tmp_path):
    sync, config, paths, fixture = _setup(tmp_path)
    trade_date = date(2026, 1, 2)

    class _NoSyncProvider(FixtureProvider):
        def probe_capabilities(self):
            raise AssertionError("fixture mode must not probe live provider")

    sync = MarketDataSync(sync.repository, _NoSyncProvider(fixture), paths)
    result = run_after_close(trade_date, config, paths, sync, fixture=fixture)
    assert result.status == "success"
    assert result.sync_steps.get("mode") == "fixture"
    assert "capability_probe" not in result.sync_steps


def test_after_close_saves_report_with_status(tmp_path):
    sync, config, paths, fixture = _setup(tmp_path)
    trade_date = date(2026, 1, 2)
    result = run_after_close(trade_date, config, paths, sync, fixture=fixture)
    assert result.report is not None
    assert result.report.status in {ScreeningStatus.OK, ScreeningStatus.EMPTY_UNIVERSE}
    store = JobStateStore(paths.home_dir / "scheduler")
    saved = store.load_report(result.job_key)
    assert saved is not None
    assert saved["status"] in {"ok", "empty_universe"}


def test_after_close_different_universe_does_not_reuse_report(tmp_path):
    sync, config, paths, fixture = _setup(tmp_path)
    trade_date = date(2026, 1, 2)
    all_request = UniverseRequest(
        universe_type=UniverseType.ALL,
        as_of=post_close_signal_time(trade_date),
    )
    custom_request = UniverseRequest(
        universe_type=UniverseType.CUSTOM,
        symbols=("600001",),
        as_of=post_close_signal_time(trade_date),
    )
    first = run_after_close(
        trade_date, config, paths, sync, fixture=fixture, universe_request=all_request,
    )
    second = run_after_close(
        trade_date, config, paths, sync, fixture=fixture, universe_request=custom_request,
    )
    assert first.skipped is False
    assert second.skipped is False
    store = JobStateStore(paths.home_dir / "scheduler")
    assert store.load_report(first.job_key) is not None
    assert store.load_report(second.job_key) is not None
    assert first.job_key.storage_id() != second.job_key.storage_id()


def test_non_trading_day_is_skipped(tmp_path):
    sync, config, paths, fixture = _setup(tmp_path)
    result = run_after_close(date(2026, 1, 4), config, paths, sync, fixture=fixture)
    assert result.status == "skipped"
    assert result.report is not None
    assert result.report.status == ScreeningStatus.DATA_ERROR
