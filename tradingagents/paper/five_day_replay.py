"""Deterministic five-day paper operations replay (Stage 6A Tier A)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import MarketOpenSnapshot, QuoteStatus
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.paper.config import PaperPaths
from tradingagents.paper.contracts import TargetPortfolioMode
from tradingagents.paper.invariants import assert_account_invariants
from tradingagents.paper.jobs import run_open_job
from tradingagents.paper.planner import RebalancePlanner
from tradingagents.paper.recovery import recover_paper_run
from tradingagents.paper.repository import PaperRepository
from tradingagents.paper.screening import STRATEGY_VERSION, build_frozen_screen_run, capture_screening_inputs
from tradingagents.paper.valuation import MarkToMarketService, ValuationStatus
from tradingagents.screener.config import ScreenerConfig
from tradingagents.screener.report import RunReport, ScreeningStatus

SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_FIXTURE = Path("tests/fixtures/paper/five_day_market.json")


@dataclass
class ReplayResult:
    fingerprint: str
    trade_days: list[str]
    fill_count: int
    order_count: int
    nav_points: int
    reports: list[str] = field(default_factory=list)


def load_scenario(path: Path | str = DEFAULT_FIXTURE) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_snapshot(symbol: str, trade_date: date, payload: dict[str, Any]) -> MarketOpenSnapshot:
    observed_at = datetime.fromisoformat(payload["observed_at"]) if payload.get("observed_at") else datetime(
        trade_date.year,
        trade_date.month,
        trade_date.day,
        9,
        35,
        tzinfo=SHANGHAI,
    )
    prev_close = float(payload.get("prev_close_cny", payload["open_cny"]))
    open_cny = float(payload["open_cny"])
    upper = payload.get("upper_limit_cny")
    lower = payload.get("lower_limit_cny")
    if upper is None:
        upper = round(prev_close * 1.1, 2)
    if lower is None:
        lower = round(prev_close * 0.9, 2)
    return MarketOpenSnapshot(
        symbol=symbol,
        trade_date=trade_date,
        observed_at=observed_at,
        open_cny=open_cny,
        prev_close_cny=prev_close,
        last_cny=float(payload.get("last_cny", open_cny)),
        cumulative_volume_shares=int(payload.get("cumulative_volume_shares", 1_000_000)),
        quote_status=QuoteStatus(payload.get("quote_status", "trading")),
        upper_limit_cny=float(upper),
        lower_limit_cny=float(lower),
        source="fixture",
        available_at=observed_at,
    )


def _open_snapshots_for_day(scenario: dict[str, Any], trade_date: date) -> dict[str, MarketOpenSnapshot]:
    raw = scenario.get("open_snapshots", {}).get(trade_date.isoformat(), {})
    return {
        symbol: _parse_snapshot(symbol, trade_date, payload)
        for symbol, payload in raw.items()
    }


def _load_market_fixture(market_repo: MarketDataRepository, scenario: dict[str, Any]) -> None:
    load_fixture_into_repository(market_repo, scenario["market"])
    corporate_rows = []
    for row in scenario.get("corporate_actions", []):
        item = dict(row)
        for key in ("ex_date", "record_date", "pay_date"):
            if item.get(key):
                item[key] = _parse_date(item[key])
        if item.get("announcement_at"):
            item["announcement_at"] = datetime.fromisoformat(item["announcement_at"])
        item["available_at"] = datetime.fromisoformat(item["available_at"])
        corporate_rows.append(item)
    if corporate_rows:
        market_repo.upsert_corporate_actions(corporate_rows)


def _plan_for_day(
    paper_repo: PaperRepository,
    market_repo: MarketDataRepository,
    *,
    scenario: dict[str, Any],
    signal_date: date,
    config: ScreenerConfig,
) -> str | None:
    plan_spec = next(
        (item for item in scenario.get("plans", []) if item["signal_date"] == signal_date.isoformat()),
        None,
    )
    if plan_spec is None:
        return None
    signal_time = post_close_signal_time(signal_date)
    screen_run_id = plan_spec.get("screen_run_id", f"screen-{signal_date.isoformat()}")
    targets = plan_spec["targets"]
    cash_weight = Decimal(str(plan_spec["cash_weight"]))
    report = RunReport(
        run_id=screen_run_id,
        status=ScreeningStatus.OK,
        signal_time=signal_time,
        data_as_of=signal_time,
        target_weights=targets,
        cash_weight=cash_weight,
    )
    frozen = build_frozen_screen_run(report, target_mode=TargetPortfolioMode.WEIGHTS)
    snapshot = paper_repo.load_account_snapshot(scenario["account_id"], as_of_date=signal_date)
    symbols = sorted(set(targets.keys()) | set(snapshot.positions))
    captures = capture_screening_inputs(
        market_repo,
        run_id=screen_run_id,
        symbols=symbols,
        trading_dates=[signal_date],
        signal_time=signal_time,
    )
    paper_repo.freeze_screen_run(frozen, captured_inputs=captures)
    planner = RebalancePlanner(paper_repo, market_repo=market_repo)
    plan = planner.plan(
        scenario["account_id"],
        screen_run_id,
        config=config,
        universe_hash=scenario["universe_hash"],
        owner_id="five-day-replay",
    )
    return plan.rebalance_run_id


def _close_day(
    paper_repo: PaperRepository,
    market_repo: MarketDataRepository,
    *,
    scenario: dict[str, Any],
    trade_date: date,
    config: ScreenerConfig,
) -> None:
    account_id = scenario["account_id"]
    owner_id = "five-day-replay"
    lease = paper_repo.acquire_account_lease(account_id, owner_id=owner_id)
    available_before = post_close_signal_time(trade_date)
    from tradingagents.market_data.contracts import CorporateActionRecord
    from tradingagents.paper.corporate_actions import CorporateActionProcessor

    snapshot = paper_repo.load_account_snapshot(account_id, as_of_date=trade_date)
    symbols = sorted(snapshot.positions)
    if symbols:
        rows = market_repo.get_corporate_actions(symbols, end=trade_date, available_before=available_before)
        processor = CorporateActionProcessor(paper_repo, account_id=account_id, owner_id=owner_id)
        for row in rows:
            action = CorporateActionRecord.model_validate(row)
            if action.pay_date != trade_date and action.ex_date != trade_date:
                continue
            processor.apply(action)

    service = MarkToMarketService(paper_repo, market_repo, owner_id=owner_id)
    valuation = service.value_account(
        account_id,
        valuation_date=trade_date,
        available_before=available_before,
        run_id=f"close:{account_id}:{trade_date.isoformat()}",
        fencing_token=lease.token,
        owner_id=owner_id,
    )
    if valuation.status != ValuationStatus.OK:
        raise RuntimeError(f"valuation failed on {trade_date}: {valuation.status.value}")
    _plan_for_day(
        paper_repo,
        market_repo,
        scenario=scenario,
        signal_date=trade_date,
        config=config,
    )


def _open_day(
    paper_repo: PaperRepository,
    market_repo: MarketDataRepository,
    *,
    scenario: dict[str, Any],
    trade_date: date,
    config: ScreenerConfig,
    crash_after_steps: set[str] | None = None,
) -> None:
    snapshots = _open_snapshots_for_day(scenario, trade_date)
    if not snapshots:
        return
    result = run_open_job(
        paper_repo,
        account_id=scenario["account_id"],
        trade_date=trade_date,
        config=config,
        universe_hash=scenario["universe_hash"],
        market_repo=market_repo,
        open_snapshots=snapshots,
        owner_id="five-day-replay",
        strategy_version=scenario.get("strategy_version", STRATEGY_VERSION),
        crash_after_steps=crash_after_steps,
    )
    if result.status.value in {"failed", "data_error"}:
        raise RuntimeError(
            f"open job failed on {trade_date}: {result.status.value} ({'; '.join(result.errors)})"
        )


def state_fingerprint(paper_repo: PaperRepository, account_id: str) -> str:
    counts = paper_repo.count_rows()
    nav_rows = paper_repo.connection.execute(
        """
        SELECT valuation_date, cash_cny, positions_value_cny, total_equity_cny,
               daily_return, cumulative_return, drawdown, valuation_manifest_hash
        FROM paper_nav_snapshots
        WHERE account_id = ?
        ORDER BY valuation_date
        """,
        [account_id],
    ).fetchall()
    fill_rows = paper_repo.connection.execute(
        """
        SELECT order_id, symbol, execution_date, quantity, price_cny,
               commission_cny, stamp_tax_cny
        FROM paper_fills
        WHERE account_id = ?
        ORDER BY execution_date, order_id, quantity
        """,
        [account_id],
    ).fetchall()
    cash_rows = paper_repo.connection.execute(
        """
        SELECT entry_type, amount_cny, component, source_id, source_type
        FROM paper_cash_ledger
        WHERE account_id = ?
        ORDER BY occurred_at, entry_type, component, amount_cny
        """,
        [account_id],
    ).fetchall()
    position_rows = paper_repo.connection.execute(
        """
        SELECT symbol, quantity_delta, cost_delta_cny, effective_date, component, source_type
        FROM paper_position_ledger
        WHERE account_id = ?
        ORDER BY effective_date, symbol, component, quantity_delta
        """,
        [account_id],
    ).fetchall()
    order_rows = paper_repo.connection.execute(
        """
        SELECT order_id, symbol, side, planned_quantity, filled_quantity,
               remaining_quantity, status, rejection_code
        FROM paper_orders
        WHERE account_id = ?
        ORDER BY order_id
        """,
        [account_id],
    ).fetchall()
    payload = {
        "counts": counts,
        "nav": [list(row) for row in nav_rows],
        "fills": [list(row) for row in fill_rows],
        "cash": [list(row) for row in cash_rows],
        "positions": [list(row) for row in position_rows],
        "orders": [list(row) for row in order_rows],
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def database_fingerprint(home_dir: Path) -> str:
    db_path = PaperPaths(home_dir=home_dir).paper_db_path
    return hashlib.sha256(db_path.read_bytes()).hexdigest()


def run_five_day_replay(
    home_dir: Path,
    scenario: dict[str, Any] | None = None,
    *,
    fixture_path: Path | str = DEFAULT_FIXTURE,
    crash_on_execution_date: date | None = None,
    recover_after_crash: bool = False,
) -> ReplayResult:
    scenario = scenario or load_scenario(fixture_path)
    home_dir = home_dir.expanduser()
    market_home = home_dir / "market"
    paper_home = home_dir / "paper"
    market_paths = MarketDataPaths(home_dir=market_home)
    market_repo = MarketDataRepository(
        market_paths.live_db_path,
        snapshot_dir=market_paths.snapshot_dir,
    )
    paper_repo = PaperRepository(PaperPaths(home_dir=paper_home))
    config = ScreenerConfig(home_dir=Path("/tmp/tradingagents-paper-replay")).model_copy(
        update={
            "universe": ScreenerConfig().universe.model_copy(
                update={"min_listing_days": 1, "min_avg_amount_20d": 1_000_000}
            ),
        }
    )
    try:
        _load_market_fixture(market_repo, scenario)
        paper_repo.create_account(
            scenario["account_id"],
            Decimal(str(scenario["initial_cash_cny"])),
        )
        trade_days = [_parse_date(day) for day in scenario["trade_days"]]
        for index, trade_day in enumerate(trade_days):
            if index > 0:
                crash_steps = (
                    {"execute_pending_orders"}
                    if crash_on_execution_date == trade_day
                    else None
                )
                try:
                    _open_day(
                        paper_repo,
                        market_repo,
                        scenario=scenario,
                        trade_date=trade_day,
                        config=config,
                        crash_after_steps=crash_steps,
                    )
                except Exception:
                    if recover_after_crash and crash_steps:
                        recover_paper_run(
                            paper_repo,
                            account_id=scenario["account_id"],
                            trade_date=trade_day,
                            config=config,
                            universe_hash=scenario["universe_hash"],
                            open_snapshots=_open_snapshots_for_day(scenario, trade_day),
                        )
                    else:
                        raise
            _close_day(
                paper_repo,
                market_repo,
                scenario=scenario,
                trade_date=trade_day,
                config=config,
            )
            assert_account_invariants(
                paper_repo.connection,
                scenario["account_id"],
                as_of_date=trade_day,
            )
        fingerprint = state_fingerprint(paper_repo, scenario["account_id"])
        counts = paper_repo.count_rows()
        nav_points = len(
            paper_repo.connection.execute(
                "SELECT 1 FROM paper_nav_snapshots WHERE account_id = ?",
                [scenario["account_id"]],
            ).fetchall()
        )
        return ReplayResult(
            fingerprint=fingerprint,
            trade_days=[day.isoformat() for day in trade_days],
            fill_count=counts["paper_fills"],
            order_count=counts["paper_orders"],
            nav_points=nav_points,
        )
    finally:
        paper_repo.close()
        market_repo.connection.close()
