"""Universe filtering must be applied inside the fixture pipeline."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.contracts import SecurityRecord
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _mini_fixture() -> dict:
    return {
        "version": 1,
        "datasets": {"daily_bars": "pit_required", "financials": "pit_required"},
        "symbols": [
            {"symbol": "GOOD", "industry": "电子", "st_flag": False, "list_date": "2025-12-31"},
            {"symbol": "STOK", "industry": "电子", "st_flag": True, "list_date": "2025-12-31"},
            {"symbol": "NEW1", "industry": "银行", "st_flag": False, "list_date": "2026-01-03"},
            {"symbol": "ILLQ", "industry": "银行", "st_flag": False, "list_date": "2025-12-31"},
            {"symbol": "SUSP", "industry": "电子", "st_flag": False, "list_date": "2025-12-31"},
            {"symbol": "DELT", "industry": "电子", "delist_after": True, "list_date": "2025-12-31"},
        ],
        "bars": {
            "2025-12-31": {
                "GOOD": {"open": 9.9, "high": 10.4, "low": 9.7, "close": 9.9, "volume": 2_000_000},
                "STOK": {"open": 9.9, "high": 10.4, "low": 9.7, "close": 9.9, "volume": 2_000_000},
                "NEW1": {"open": 9.9, "high": 10.4, "low": 9.7, "close": 9.9, "volume": 2_000_000},
                "ILLQ": {"open": 9.9, "high": 10.4, "low": 9.7, "close": 9.9, "volume": 100},
                "SUSP": {"open": 9.9, "high": 10.4, "low": 9.7, "close": 9.9, "volume": 2_000_000, "suspended": True},
                "DELT": {"open": 9.9, "high": 10.4, "low": 9.7, "close": 9.9, "volume": 2_000_000},
            },
            "2026-01-02": {
                "GOOD": {"open": 10, "high": 10.5, "low": 9.8, "close": 10, "volume": 2_000_000},
                "STOK": {"open": 10, "high": 10.5, "low": 9.8, "close": 10, "volume": 2_000_000},
                "NEW1": {"open": 10, "high": 10.5, "low": 9.8, "close": 10, "volume": 2_000_000},
                "ILLQ": {"open": 10, "high": 10.5, "low": 9.8, "close": 10, "volume": 100},
                "SUSP": {"open": 10, "high": 10.5, "low": 9.8, "close": 10, "volume": 2_000_000, "suspended": True},
                "DELT": {"open": 10, "high": 10.5, "low": 9.8, "close": 10, "volume": 2_000_000},
            },
            "2026-01-03": {
                "GOOD": {"open": 10.1, "high": 10.6, "low": 9.9, "close": 10.2, "volume": 2_000_000},
                "STOK": {"open": 10.1, "high": 10.6, "low": 9.9, "close": 10.2, "volume": 2_000_000},
                "NEW1": {"open": 10.1, "high": 10.6, "low": 9.9, "close": 10.2, "volume": 2_000_000},
                "ILLQ": {"open": 10.1, "high": 10.6, "low": 9.9, "close": 10.2, "volume": 100},
                "SUSP": {"open": 10.1, "high": 10.6, "low": 9.9, "close": 10.2, "volume": 2_000_000, "suspended": True},
                "DELT": {"open": 10.1, "high": 10.6, "low": 9.9, "close": 10.2, "volume": 2_000_000},
            },
            "2026-01-06": {
                "GOOD": {"open": 10.2, "high": 10.7, "low": 10.0, "close": 10.3, "volume": 2_000_000},
                "STOK": {"open": 10.2, "high": 10.7, "low": 10.0, "close": 10.3, "volume": 2_000_000},
                "NEW1": {"open": 10.2, "high": 10.7, "low": 10.0, "close": 10.3, "volume": 2_000_000},
                "ILLQ": {"open": 10.2, "high": 10.7, "low": 10.0, "close": 10.3, "volume": 100},
                "SUSP": {"open": 10.2, "high": 10.7, "low": 10.0, "close": 10.3, "volume": 2_000_000, "suspended": True},
                "DELT": {"open": 10.2, "high": 10.7, "low": 10.0, "close": 10.3, "volume": 2_000_000},
            },
        },
        "financials": [],
        "delistings": {"2026-02-01": ["DELT"]},
    }


def test_pipeline_excludes_st_suspended_new_listing_and_illiquid(tmp_path):
    base = ScreenerConfig()
    config = base.model_copy(update={
        "universe": base.universe.model_copy(update={
            "min_listing_days": 2,
            "min_avg_amount_20d": 1_000_000,
        })
    })
    result = run_fixture_backtest(_mini_fixture(), config, tmp_path / "universe.duckdb")
    assert set(result["ranking"]) == {"GOOD", "DELT"}
    assert result["excluded_reasons"]["STOK"] == ["st"]
    assert result["excluded_reasons"]["SUSP"] == ["suspended"]
    assert result["excluded_reasons"]["ILLQ"] == ["illiquid"]
    assert result["excluded_reasons"]["NEW1"] == ["new_listing"]
    assert "DELT" in result["target_weights"]


def test_future_security_available_at_does_not_affect_historical_pool(tmp_path):
    fixture = _mini_fixture()
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    load_fixture_into_repository(repo, fixture)
    signal_date = date(2026, 1, 3)
    signal_time = post_close_signal_time(signal_date)
    repo.upsert_security_records([
        SecurityRecord(
            symbol="LATE",
            name="LATE",
            board="main",
            valid_from=date(2026, 1, 1),
            valid_to=None,
            list_date=date(2026, 1, 1),
            delist_date=None,
            status="listed",
            st_flag=False,
            available_at=datetime(2099, 1, 1, tzinfo=SHANGHAI),
            source="fixture",
        )
    ])
    symbols = repo.list_effective_symbols(signal_date, signal_time)
    assert "LATE" not in symbols
    assert "DELT" in symbols


def test_all_filtered_returns_empty_portfolio_without_crash(tmp_path):
    fixture = _mini_fixture()
    for meta in fixture["symbols"]:
        meta["st_flag"] = True
    result = run_fixture_backtest(fixture, ScreenerConfig(), tmp_path / "empty.duckdb")
    assert result["positions"] == 0
    assert result["target_weights"] == {}
    assert result["cash_weight"] == pytest.approx(1.0)
