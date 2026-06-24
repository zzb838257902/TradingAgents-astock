"""Stage 4 business equivalence when event enrichment is disabled (phase 5 Task 0)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest, run_screen
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

FIXTURE = Path("tests/fixtures/screener/mvp_market.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")

STAGE4_EQUIVALENCE_KEYS = (
    "excluded_reasons",
    "ranking",
    "target_weights",
    "cash_weight",
    "top_symbol",
    "positions",
    "orders",
    "metrics",
    "industry_by_symbol",
)


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


def _config_with_disabled_event_metadata() -> ScreenerConfig:
    base = _relaxed_config()
    return base.model_copy(update={
        "event_enrichment": base.event_enrichment.model_copy(update={
            "candidate_limit": 50,
            "event_weight": 0.15,
            "event_half_life_days": 14,
        }),
    })


def test_disabled_event_enrichment_preserves_fixture_backtest_output(tmp_path: Path):
    fixture = _load_fixture()
    baseline = run_fixture_backtest(
        fixture,
        _relaxed_config(),
        tmp_path / "baseline.duckdb",
    )
    with_metadata = run_fixture_backtest(
        fixture,
        _config_with_disabled_event_metadata(),
        tmp_path / "metadata.duckdb",
    )
    for key in STAGE4_EQUIVALENCE_KEYS:
        assert baseline[key] == with_metadata[key], key


def test_disabled_event_enrichment_preserves_screen_report_fields(tmp_path: Path):
    fixture = _load_fixture()
    trading_dates = sorted(fixture["bars"])
    signal_date = date.fromisoformat(trading_dates[-2])
    signal_time = datetime.combine(
        signal_date,
        datetime.min.time().replace(hour=15, minute=30),
        tzinfo=SHANGHAI,
    )
    request = UniverseRequest(universe_type=UniverseType.ALL, as_of=signal_time)
    baseline = run_screen(
        fixture,
        _relaxed_config(),
        tmp_path / "screen_base.duckdb",
        universe_request=request,
    )
    with_metadata = run_screen(
        fixture,
        _config_with_disabled_event_metadata(),
        tmp_path / "screen_meta.duckdb",
        universe_request=request,
    )
    assert baseline.excluded_reasons == with_metadata.excluded_reasons
    assert baseline.ranking == with_metadata.ranking
    assert baseline.target_weights == with_metadata.target_weights
    assert baseline.cash_weight == with_metadata.cash_weight
    assert baseline.factor_contributions == with_metadata.factor_contributions


def test_signal_time_is_deterministic_not_wall_clock(tmp_path: Path):
    fixture = _load_fixture()
    report = run_screen(fixture, _relaxed_config(), tmp_path / "signal.duckdb")
    trading_dates = sorted(fixture["bars"])
    expected_date = date.fromisoformat(trading_dates[-2])
    assert report.signal_time.date() == expected_date
    assert report.signal_time.hour == 15
    assert report.signal_time.minute == 30
    assert report.signal_time.tzinfo is not None


def test_pit_error_path_uses_deterministic_signal_time_not_now(tmp_path: Path):
    fixture = _load_fixture()
    broken = json.loads(json.dumps(fixture))
    broken["datasets"]["daily_bars"] = "best_effort"
    report = run_screen(broken, _relaxed_config(), tmp_path / "pit_error.duckdb")
    trading_dates = sorted(fixture["bars"])
    expected_date = date.fromisoformat(trading_dates[-2])
    assert report.signal_time.date() == expected_date
    assert report.signal_time.hour == 15
