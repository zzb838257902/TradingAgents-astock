"""Pipeline integration for industry/index universe screening."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _universe_fixture() -> dict:
    return {
        "version": 1,
        "datasets": {"daily_bars": "pit_required", "financials": "pit_required"},
        "symbols": [
            {"symbol": "600001", "industry": "电子", "list_date": "2025-12-31"},
            {"symbol": "600002", "industry": "电子", "list_date": "2025-12-31"},
            {"symbol": "600003", "industry": "银行", "list_date": "2025-12-31"},
        ],
        "bars": {
            "2025-12-31": {
                "600001": {"open": 10, "high": 10.5, "low": 9.8, "close": 10, "volume": 2_000_000},
                "600002": {"open": 20, "high": 20.5, "low": 19.8, "close": 20, "volume": 2_000_000},
                "600003": {"open": 30, "high": 30.5, "low": 29.8, "close": 30, "volume": 2_000_000},
            },
            "2026-01-02": {
                "600001": {"open": 10.1, "high": 10.6, "low": 9.9, "close": 10.2, "volume": 2_000_000},
                "600002": {"open": 20.1, "high": 20.6, "low": 19.9, "close": 20.2, "volume": 2_000_000},
                "600003": {"open": 30.1, "high": 30.6, "low": 29.9, "close": 30.2, "volume": 2_000_000},
            },
            "2026-01-03": {
                "600001": {"open": 10.2, "high": 10.7, "low": 10.0, "close": 10.3, "volume": 2_000_000},
                "600002": {"open": 20.2, "high": 20.7, "low": 20.0, "close": 20.3, "volume": 2_000_000},
                "600003": {"open": 30.2, "high": 30.7, "low": 30.0, "close": 30.3, "volume": 2_000_000},
            },
            "2026-01-06": {
                "600001": {"open": 10.3, "high": 10.8, "low": 10.1, "close": 10.4, "volume": 2_000_000},
                "600002": {"open": 20.3, "high": 20.8, "low": 20.1, "close": 20.4, "volume": 2_000_000},
                "600003": {"open": 30.3, "high": 30.8, "low": 30.1, "close": 30.4, "volume": 2_000_000},
            },
        },
        "financials": [
            {
                "symbol": "600001",
                "report_period": "2025-09-30",
                "available_at": "2025-10-29T20:00:00+08:00",
                "roe": 0.15,
                "operating_cashflow": 120,
                "net_profit": 100,
                "debt_ratio": 0.35,
            },
            {
                "symbol": "600002",
                "report_period": "2025-09-30",
                "available_at": "2025-10-29T20:00:00+08:00",
                "roe": 0.12,
                "operating_cashflow": 90,
                "net_profit": 80,
                "debt_ratio": 0.40,
            },
            {
                "symbol": "600003",
                "report_period": "2025-09-30",
                "available_at": "2025-10-29T20:00:00+08:00",
                "roe": 0.10,
                "operating_cashflow": 70,
                "net_profit": 60,
                "debt_ratio": 0.45,
            },
        ],
        "board_definitions": [
            {"board_type": "industry", "board_code": "801080.SI", "name": "电子", "pit_level": "pit_required"},
        ],
        "board_memberships": [
            {
                "board_type": "industry",
                "board_code": "801080.SI",
                "symbol": "600001",
                "membership_mode": "effective_interval",
                "effective_from": "2025-01-01",
                "available_at": "2025-01-01T09:00:00+08:00",
            },
            {
                "board_type": "industry",
                "board_code": "801080.SI",
                "symbol": "600002",
                "membership_mode": "effective_interval",
                "effective_from": "2025-01-01",
                "available_at": "2025-01-01T09:00:00+08:00",
            },
        ],
    }


def test_industry_universe_limits_ranking(tmp_path):
    fixture = _universe_fixture()
    base = ScreenerConfig()
    config = base.model_copy(update={
        "universe": base.universe.model_copy(update={
            "min_listing_days": 2,
            "min_avg_amount_20d": 1_000_000,
        }),
    })
    all_result = run_fixture_backtest(fixture, config, tmp_path / "all.duckdb")
    industry_result = run_fixture_backtest(
        fixture,
        config,
        tmp_path / "industry.duckdb",
        universe_request=UniverseRequest(
            universe_type=UniverseType.INDUSTRY,
            universe_code="801080.SI",
            as_of=datetime(2026, 1, 6, 15, 30, tzinfo=SHANGHAI),
        ),
    )
    assert all_result["ranking"] == ["600001", "600002", "600003"]
    assert industry_result["ranking"] == ["600001", "600002"]


def test_concept_universe_limits_ranking(tmp_path):
    fixture = _universe_fixture()
    fixture["board_definitions"] = [
        {"board_type": "concept", "board_code": "BK1184.DC", "name": "测试概念", "pit_level": "pit_required"},
    ]
    fixture["board_memberships"] = [
        {
            "board_type": "concept",
            "board_code": "BK1184.DC",
            "symbol": "600001",
            "membership_mode": "dated_snapshot",
            "snapshot_date": "2026-01-03",
            "available_at": "2026-01-03T15:30:00+08:00",
        },
    ]
    base = ScreenerConfig()
    config = base.model_copy(update={
        "universe": base.universe.model_copy(update={
            "min_listing_days": 2,
            "min_avg_amount_20d": 1_000_000,
        }),
    })
    result = run_fixture_backtest(
        fixture,
        config,
        tmp_path / "concept.duckdb",
        universe_request=UniverseRequest(
            universe_type=UniverseType.CONCEPT,
            universe_code="BK1184.DC",
            as_of=datetime(2026, 1, 6, 15, 30, tzinfo=SHANGHAI),
        ),
    )
    assert result["ranking"] == ["600001"]
