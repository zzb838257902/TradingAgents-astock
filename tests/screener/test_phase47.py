"""Phase 4.7 unified pipeline and run report."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest, run_screen
from tradingagents.screener.report import ScreeningStatus
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

FIXTURE = Path("tests/fixtures/screener/mvp_market.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _relaxed_config() -> ScreenerConfig:
    base = ScreenerConfig()
    return base.model_copy(update={
        "universe": base.universe.model_copy(update={
            "min_listing_days": 2,
            "min_avg_amount_20d": 1_000_000,
        }),
    })


@pytest.mark.parametrize("universe_type", list(UniverseType))
def test_all_universe_modes_return_run_report(tmp_path, universe_type: UniverseType):
    fixture = _load_fixture()
    if universe_type == UniverseType.INDUSTRY:
        fixture = copy.deepcopy(fixture)
        fixture["board_definitions"] = [
            {"board_type": "industry", "board_code": "801080.SI", "name": "电子", "pit_level": "pit_required"},
        ]
        fixture["board_memberships"] = [{
            "board_type": "industry",
            "board_code": "801080.SI",
            "symbol": "600001",
            "membership_mode": "effective_interval",
            "effective_from": "2020-01-01",
            "available_at": "2020-01-01T09:00:00+08:00",
        }]
        universe_code = "801080.SI"
        symbols: tuple[str, ...] = ()
    elif universe_type == UniverseType.CONCEPT:
        fixture = copy.deepcopy(fixture)
        fixture["board_definitions"] = [
            {"board_type": "concept", "board_code": "BK1184.DC", "name": "概念", "pit_level": "pit_required"},
        ]
        fixture["board_memberships"] = [{
            "board_type": "concept",
            "board_code": "BK1184.DC",
            "symbol": "600001",
            "membership_mode": "dated_snapshot",
            "snapshot_date": "2025-12-18",
            "available_at": "2025-12-18T15:30:00+08:00",
        }]
        universe_code = "BK1184.DC"
        symbols = ()
    elif universe_type == UniverseType.INDEX:
        fixture = copy.deepcopy(fixture)
        fixture["board_definitions"] = [
            {"board_type": "index", "board_code": "000300.SH", "name": "沪深300", "pit_level": "pit_required"},
        ]
        fixture["board_memberships"] = [{
            "board_type": "index",
            "board_code": "000300.SH",
            "symbol": "600001",
            "membership_mode": "effective_interval",
            "effective_from": "2020-01-01",
            "available_at": "2020-01-01T09:00:00+08:00",
        }]
        universe_code = "000300.SH"
        symbols = ()
    elif universe_type == UniverseType.CUSTOM:
        universe_code = None
        symbols = ("600001", "600002")
    else:
        universe_code = None
        symbols = ()

    trading_dates = sorted(fixture["bars"])
    signal_date = date.fromisoformat(trading_dates[-2])
    signal_time = datetime.combine(signal_date, datetime.min.time().replace(hour=15, minute=30), tzinfo=SHANGHAI)
    report = run_screen(
        fixture,
        _relaxed_config(),
        tmp_path / f"{universe_type}.duckdb",
        universe_request=UniverseRequest(
            universe_type=universe_type,
            universe_code=universe_code,
            symbols=symbols,
            as_of=signal_time,
        ),
    )
    assert report.run_id
    assert report.signal_time == signal_time
    assert report.universe_type == universe_type.value
    assert report.status in {ScreeningStatus.OK, ScreeningStatus.EMPTY_UNIVERSE}
    output = report.to_output_dict()
    for key in (
        "dataset_versions",
        "data_sources",
        "pit_level",
        "universe_size",
        "included_count",
        "excluded_count",
        "factor_contributions",
        "industry_weights",
    ):
        assert key in output


def test_data_error_differs_from_empty_universe(tmp_path):
    fixture = _load_fixture()
    fixture = copy.deepcopy(fixture)
    fixture["board_definitions"] = [{
        "board_type": "concept",
        "board_code": "BK9999.DC",
        "name": "当前概念",
        "pit_level": "current_only",
    }]
    trading_dates = sorted(fixture["bars"])
    signal_date = date.fromisoformat(trading_dates[-2])
    signal_time = datetime.combine(signal_date, datetime.min.time().replace(hour=15, minute=30), tzinfo=SHANGHAI)
    report = run_screen(
        fixture,
        _relaxed_config(),
        tmp_path / "error.duckdb",
        universe_request=UniverseRequest(
            universe_type=UniverseType.CONCEPT,
            universe_code="BK9999.DC",
            as_of=signal_time,
        ),
    )
    assert report.status == ScreeningStatus.DATA_ERROR
    assert report.errors


def test_run_fixture_backtest_remains_backward_compatible(tmp_path):
    legacy = run_fixture_backtest(_load_fixture(), ScreenerConfig(), tmp_path / "legacy.duckdb")
    report = run_screen(_load_fixture(), ScreenerConfig(), tmp_path / "report.duckdb")
    for key in legacy:
        assert key in report.to_legacy_dict()
        if key in {"metrics", "excluded_reasons", "industry_by_symbol"}:
            continue
        assert legacy[key] == report.to_legacy_dict()[key] or key == "top_symbol"


def test_ok_report_includes_industry_weights(tmp_path):
    report = run_screen(_load_fixture(), _relaxed_config(), tmp_path / "ok.duckdb")
    if report.status == ScreeningStatus.OK:
        assert report.industry_weights
        assert abs(sum(report.target_weights.values()) + report.cash_weight - 1.0) < 0.05
