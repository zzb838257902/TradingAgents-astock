"""Phase 4.3b auxiliary datasets, price limit audit, and fixture equivalence."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradingagents.market_data.fixture_store import load_fixture_as_published
from tradingagents.market_data.quality import audit_price_limits
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest

FIXTURE = Path("tests/fixtures/screener/mvp_market.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_security_status_history_overrides_current_st_flag(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_security_status_history([{
        "symbol": "600001",
        "status": "ST",
        "effective_from": date(2025, 1, 1),
        "effective_to": None,
        "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
        "source": "fixture",
    }])
    assert repo.is_st_on(
        "600001",
        date(2025, 6, 1),
        datetime(2025, 6, 1, 15, 30, tzinfo=SHANGHAI),
    )
    assert not repo.is_st_on(
        "600002",
        date(2025, 6, 1),
        datetime(2025, 6, 1, 15, 30, tzinfo=SHANGHAI),
    )


def test_suspension_events_mark_symbol_suspended(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_suspension_events([{
        "symbol": "600002",
        "start_date": date(2025, 12, 1),
        "end_date": date(2025, 12, 31),
        "reason": "temporary",
        "available_at": datetime(2025, 12, 1, 9, 0, tzinfo=SHANGHAI),
        "source": "fixture",
    }])
    assert repo.is_suspended_on(
        "600002",
        date(2025, 12, 15),
        datetime(2025, 12, 15, 15, 30, tzinfo=SHANGHAI),
    )
    assert not repo.is_suspended_on(
        "600002",
        date(2026, 1, 2),
        datetime(2026, 1, 2, 15, 30, tzinfo=SHANGHAI),
    )


def test_price_limit_audit_records_mismatch(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    issues = audit_price_limits([{
        "symbol": "600001",
        "trade_date": date(2025, 12, 18),
        "prev_close": 10.0,
        "supplier_limit_up": 11.5,
        "supplier_limit_down": 9.0,
        "st_flag": False,
        "board": "main",
    }])
    assert issues
    repo.record_quality_event(
        dataset="price_limits",
        rule="limit_price_mismatch",
        severity="warning",
        detail_json=issues[0],
    )
    events = repo.list_quality_events("price_limits")
    assert len(events) == 1
    assert events[0]["rule"] == "limit_price_mismatch"


def test_adjustment_factors_and_corporate_actions_round_trip(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_adjustment_factors([{
        "symbol": "600001",
        "trade_date": date(2025, 12, 18),
        "factor": 1.02,
        "available_at": datetime(2025, 12, 18, 15, 30, tzinfo=SHANGHAI),
        "source": "fixture",
    }])
    repo.upsert_corporate_actions([{
        "symbol": "600001",
        "ex_date": date(2025, 12, 20),
        "action_type": "cash_div",
        "cash_div": 0.5,
        "stock_div": None,
        "split_ratio": None,
        "rights_ratio": None,
        "available_at": datetime(2025, 12, 20, 9, 0, tzinfo=SHANGHAI),
        "source": "fixture",
    }])
    factors = repo.get_adjustment_factors(
        ["600001"],
        end=date(2025, 12, 18),
        available_before=datetime(2025, 12, 18, 16, 0, tzinfo=SHANGHAI),
    )
    actions = repo.get_corporate_actions(
        ["600001"],
        end=date(2025, 12, 20),
        available_before=datetime(2025, 12, 20, 16, 0, tzinfo=SHANGHAI),
    )
    assert factors[0]["factor"] == pytest.approx(1.02)
    assert actions[0]["action_type"] == "cash_div"


def test_fixture_legacy_and_published_entries_are_equivalent(tmp_path):
    fixture = _load_fixture()
    config = ScreenerConfig()
    legacy = run_fixture_backtest(fixture, config, tmp_path / "legacy.duckdb")
    published_repo = MarketDataRepository(tmp_path / "published.duckdb")
    load_fixture_as_published(published_repo, fixture)
    published = run_fixture_backtest(
        fixture, config, tmp_path / "published.duckdb", reload=False
    )
    assert legacy["ranking"] == published["ranking"]
    assert legacy["excluded_reasons"] == published["excluded_reasons"]
    assert legacy["target_weights"] == published["target_weights"]
    assert legacy["cash_weight"] == published["cash_weight"]
    assert legacy["orders"] == published["orders"]
    for key in legacy["metrics"]:
        assert legacy["metrics"][key] == pytest.approx(published["metrics"][key], rel=1e-9, abs=1e-9)
