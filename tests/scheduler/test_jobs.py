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
from tradingagents.scheduler.jobs import config_hash, run_after_close
from tradingagents.scheduler.state import JobKey, JobStateStore
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.report import ScreeningStatus

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
    key = JobKey("after_close", trade_date, config_hash(config))
    assert len(store.load_run(key)["attempts"]) == 1


def test_after_close_force_rerun_creates_second_attempt(tmp_path):
    sync, config, paths, fixture = _setup(tmp_path)
    trade_date = date(2026, 1, 2)
    run_after_close(trade_date, config, paths, sync, fixture=fixture)
    run_after_close(trade_date, config, paths, sync, fixture=fixture, force=True)
    store = JobStateStore(paths.home_dir / "scheduler")
    key = JobKey("after_close", trade_date, config_hash(config))
    assert len(store.load_run(key)["attempts"]) == 2


def test_after_close_saves_report_with_status(tmp_path):
    sync, config, paths, fixture = _setup(tmp_path)
    trade_date = date(2026, 1, 2)
    result = run_after_close(trade_date, config, paths, sync, fixture=fixture)
    assert result.report is not None
    assert result.report.status in {ScreeningStatus.OK, ScreeningStatus.EMPTY_UNIVERSE}
    store = JobStateStore(paths.home_dir / "scheduler")
    key = JobKey("after_close", trade_date, config_hash(config))
    saved = store.load_report(key)
    assert saved is not None
    assert saved["status"] in {"ok", "empty_universe"}


def test_non_trading_day_is_skipped(tmp_path):
    sync, config, paths, fixture = _setup(tmp_path)
    result = run_after_close(date(2026, 1, 4), config, paths, sync, fixture=fixture)
    assert result.status == "skipped"
    assert result.report is not None
    assert result.report.status == ScreeningStatus.DATA_ERROR
