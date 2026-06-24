"""Stage 6A acceptance helpers used by tests and scripts."""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import MarketOpenSnapshot, QuoteStatus
from tradingagents.market_data.market_hours import post_close_signal_time
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.paper.config import PaperPaths
from tradingagents.paper.contracts import OrderSide, OrderStatus, PaperOrder
from tradingagents.paper.execution import PaperExecutionEngine
from tradingagents.paper.five_day_replay import (
    DEFAULT_FIXTURE,
    load_scenario,
    run_five_day_replay,
)
from tradingagents.paper.migrations import apply_paper_migrations
from tradingagents.paper.reporting import PaperReportRun, PaperReportWriter
from tradingagents.paper.repository import PaperRepository, RebalanceRevisionSpec
from tradingagents.paper.valuation import MarkToMarketService
from tests.paper.conftest import (
    SIGNAL_TIME,
    TRADE_DATE,
    create_rebalance_with_lease,
    insert_orders_with_lease,
    position_entry,
    rebuild_projection_with_lease,
    seed_demo_account,
    append_position_with_lease,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def check_paper_migrations() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "paper.duckdb"
        version = apply_paper_migrations(path)
        ok = path.exists() and version > 0
        return {"name": "paper_migrations", "ok": ok, "version": version}


def check_five_day_replay(fixture_path: Path = DEFAULT_FIXTURE) -> dict:
    scenario = load_scenario(fixture_path)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        first = run_five_day_replay(tmp_path / "a", scenario=scenario)
        second = run_five_day_replay(tmp_path / "b", scenario=scenario)
        expected = scenario.get("expected_fingerprint")
        ok = first.fingerprint == second.fingerprint
        if expected:
            ok = ok and first.fingerprint == expected
        return {
            "name": "five_day_replay",
            "ok": ok,
            "fingerprint": first.fingerprint,
            "fill_count": first.fill_count,
            "nav_points": first.nav_points,
        }


def check_crash_recovery(fixture_path: Path = DEFAULT_FIXTURE) -> dict:
    scenario = load_scenario(fixture_path)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        golden = run_five_day_replay(tmp_path / "golden", scenario=scenario)
        recovered = run_five_day_replay(
            tmp_path / "recovered",
            scenario=scenario,
            crash_on_execution_date=date(2026, 1, 8),
            recover_after_crash=True,
        )
        return {
            "name": "crash_recovery",
            "ok": golden.fingerprint == recovered.fingerprint,
            "fingerprint": golden.fingerprint,
        }


def check_report_atomic_write() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        paper_repo = PaperRepository(PaperPaths(home_dir=tmp_path / "paper"))
        paper_repo.create_account("demo", Decimal("1000000"))
        writer = PaperReportWriter(tmp_path)
        run = PaperReportRun(
            account_id="demo",
            trade_date=date(2026, 1, 6),
            logical_run_key="demo:acceptance",
            revision=1,
        )
        manifest = writer.write(run, paper_repo=paper_repo)
        revision_two = writer.write(
            PaperReportRun(
                account_id="demo",
                trade_date=date(2026, 1, 6),
                logical_run_key="demo:acceptance",
                revision=2,
            ),
            paper_repo=paper_repo,
        )
        latest = json.loads((revision_two.parents[1] / "latest.json").read_text(encoding="utf-8"))
        paper_repo.close()
        ok = manifest != revision_two and latest["revision"] == 2
        return {"name": "report_atomic_write", "ok": ok}


def check_limit_reject_execution() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        repo = PaperRepository(PaperPaths(home_dir=tmp_path))
        seed_demo_account(repo)
        create_rebalance_with_lease(
            repo,
            RebalanceRevisionSpec(
                rebalance_run_id="reb-limit",
                account_id="demo",
                screen_run_id="screen-limit",
                screen_content_hash="hash",
                target_hash="target",
                signal_date=SIGNAL_TIME.date(),
                signal_time=SIGNAL_TIME,
                execution_date=TRADE_DATE,
                universe_hash="uni",
                config_hash="cfg",
                strategy_version="v1",
                target_weights_json='{"600002": 0.1}',
                logical_run_key="demo:limit",
                revision=1,
            ),
        )
        insert_orders_with_lease(
            repo,
            [
                PaperOrder(
                    order_id="ord-buy-600002-reb-limit",
                    rebalance_run_id="reb-limit",
                    account_id="demo",
                    symbol="600002",
                    side=OrderSide.BUY,
                    planned_quantity=1000,
                    remaining_quantity=1000,
                    reference_price_cny=Decimal("10.00"),
                    status=OrderStatus.PENDING,
                )
            ],
        )
        repo.expire_lease_for_test("demo")
        lease = repo.acquire_account_lease("demo", owner_id="test")
        observed = datetime(2026, 6, 23, 9, 35, tzinfo=SHANGHAI)
        snap = MarketOpenSnapshot(
            symbol="600002",
            trade_date=TRADE_DATE,
            observed_at=observed,
            open_cny=Decimal("11.00"),
            prev_close_cny=Decimal("10.00"),
            last_cny=Decimal("11.00"),
            cumulative_volume_shares=1_000_000,
            quote_status=QuoteStatus.TRADING,
            upper_limit_cny=Decimal("11.00"),
            lower_limit_cny=Decimal("9.00"),
            source="fixture",
            available_at=observed,
        )
        engine = PaperExecutionEngine()
        engine.execute_rebalance(
            repo,
            rebalance_run_id="reb-limit",
            execution_date=TRADE_DATE,
            execution_time=observed,
            fencing_token=lease.token,
            owner_id="test",
            snapshots={"600002": snap},
        )
        order = repo.list_orders_for_rebalance("reb-limit")[0]
        repo.close()
        return {
            "name": "limit_reject",
            "ok": order.status == OrderStatus.REJECTED and order.rejection_code == "LIMIT_UP",
        }


def check_missing_price_rejected(scenario: dict | None = None) -> dict:
    scenario = scenario or load_scenario()
    edge = scenario.get("edge_cases", {}).get("missing_price", {})
    valuation_date = date.fromisoformat(edge.get("valuation_date", "2026-01-09"))
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        market_paths = MarketDataPaths(home_dir=tmp_path / "market")
        market_repo = MarketDataRepository(
            market_paths.live_db_path,
            snapshot_dir=market_paths.snapshot_dir,
        )
        from tradingagents.paper.five_day_replay import _load_market_fixture

        _load_market_fixture(market_repo, scenario)
        paper_repo = PaperRepository(PaperPaths(home_dir=tmp_path / "paper"))
        paper_repo.create_account("demo", Decimal("1000000"))
        append_position_with_lease(
            paper_repo,
            position_entry(symbol=edge.get("symbol", "600005"), quantity_delta=100),
        )
        rebuild_projection_with_lease(paper_repo, as_of_date=valuation_date)
        paper_repo.expire_lease_for_test("demo")
        lease = paper_repo.acquire_account_lease("demo", owner_id="test")
        service = MarkToMarketService(paper_repo, market_repo)
        try:
            service.value_account(
                "demo",
                valuation_date=valuation_date,
                available_before=post_close_signal_time(valuation_date),
                run_id="acceptance-missing-price",
                fencing_token=lease.token,
                owner_id="test",
            )
            ok = False
        except Exception:
            ok = True
        paper_repo.close()
        market_repo.connection.close()
        return {"name": "missing_price_rejected", "ok": ok}


def run_offline_acceptance(fixture_path: Path = DEFAULT_FIXTURE) -> dict:
    steps = [
        check_paper_migrations(),
        check_five_day_replay(fixture_path),
        check_crash_recovery(fixture_path),
        check_report_atomic_write(),
        check_limit_reject_execution(),
        check_missing_price_rejected(load_scenario(fixture_path)),
    ]
    return {
        "tier": "A",
        "passed": all(step["ok"] for step in steps),
        "steps": steps,
    }
