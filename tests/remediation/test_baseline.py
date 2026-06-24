"""Frozen pre-fix baseline for existing-defects remediation (Task 0)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import cli.main as main_cli
from cli.models import AnalystType
from cli.utils import ANALYST_CHOICES as CLI_ANALYST_CHOICES
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.pipeline import run_fixture_backtest, run_screen
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseType

from tests.events.test_stage4_equivalence import STAGE4_EQUIVALENCE_KEYS

FIXTURE = Path("tests/fixtures/screener/mvp_market.json")
SHANGHAI = ZoneInfo("Asia/Shanghai")

FROZEN_FIXTURE_SHA256 = (
    "42e43a4ba99c8d81812aaa0fb875d2f70072e5555f984a1cadd0680ad6731b6e"
)
FROZEN_DEFAULT_SCREEN_SHA256 = (
    "339f144bf7120d678a5c86e78b801fe99492c6c70732131ffdbce8800545381b"
)

SCREEN_HASH_KEYS = (
    "excluded_reasons",
    "ranking",
    "target_weights",
    "cash_weight",
    "factor_contributions",
    "top_symbol",
    "positions",
    "metrics",
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


def _screen_output_hash(report) -> str:
    payload = {key: getattr(report, key) for key in SCREEN_HASH_KEYS}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_fixture_sha256_is_frozen():
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert digest == FROZEN_FIXTURE_SHA256


def test_default_screen_output_hash_is_frozen(tmp_path: Path):
    fixture = _load_fixture()
    trading_dates = sorted(fixture["bars"])
    signal_date = date.fromisoformat(trading_dates[-2])
    signal_time = datetime.combine(
        signal_date,
        datetime.min.time().replace(hour=15, minute=30),
        tzinfo=SHANGHAI,
    )
    request = UniverseRequest(universe_type=UniverseType.ALL, as_of=signal_time)
    report = run_screen(
        fixture,
        _relaxed_config(),
        tmp_path / "baseline_screen.duckdb",
        universe_request=request,
    )
    assert _screen_output_hash(report) == FROZEN_DEFAULT_SCREEN_SHA256


def test_disabled_event_enrichment_preserves_stage4_equivalence(tmp_path: Path):
    fixture = _load_fixture()
    baseline = run_fixture_backtest(
        fixture,
        _relaxed_config(),
        tmp_path / "baseline.duckdb",
    )
    with_metadata = run_fixture_backtest(
        fixture,
        _relaxed_config().model_copy(update={
            "event_enrichment": _relaxed_config().event_enrichment.model_copy(update={
                "candidate_limit": 50,
                "event_weight": 0.15,
                "event_half_life_days": 14,
            }),
        }),
        tmp_path / "metadata.duckdb",
    )
    for key in STAGE4_EQUIVALENCE_KEYS:
        assert baseline[key] == with_metadata[key], key


def test_cli_exposes_seven_analysts_with_frozen_mappings():
    assert main_cli.ANALYST_ORDER == [
        "market",
        "social",
        "news",
        "fundamentals",
        "policy",
        "hot_money",
        "lockup",
    ]
    assert main_cli.ANALYST_AGENT_NAMES == {
        "market": "Market Analyst",
        "social": "Social Analyst",
        "news": "News Analyst",
        "fundamentals": "Fundamentals Analyst",
        "policy": "Policy Analyst",
        "hot_money": "Hot_money Analyst",
        "lockup": "Lockup Analyst",
    }
    assert main_cli.ANALYST_REPORT_MAP == {
        "market": "market_report",
        "social": "sentiment_report",
        "news": "news_report",
        "fundamentals": "fundamentals_report",
        "policy": "policy_report",
        "hot_money": "hot_money_report",
        "lockup": "lockup_report",
    }
    assert len(CLI_ANALYST_CHOICES) == 7
    assert [value for _, value in CLI_ANALYST_CHOICES] == [
        AnalystType.MARKET,
        AnalystType.SOCIAL,
        AnalystType.NEWS,
        AnalystType.FUNDAMENTALS,
        AnalystType.POLICY,
        AnalystType.HOT_MONEY,
        AnalystType.LOCKUP,
    ]
    assert main_cli.ANALYST_REPORT_MAP["social"] == "sentiment_report"
