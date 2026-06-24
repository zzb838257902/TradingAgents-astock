"""Paper reporting tests (Stage 6A Task 7)."""

from __future__ import annotations

import json
from decimal import Decimal

from tradingagents.paper.execution import PaperExecutionEngine
from tradingagents.paper.reporting import PaperReportRun, PaperReportWriter
from tradingagents.paper.repository import PaperRepository
from tests.paper.conftest import (
    EXECUTION_TIME,
    SIGNAL_TIME,
    TRADE_DATE,
    acquire_test_lease,
    seed_execution_orders,
)
from tests.paper.test_jobs import open_snapshot


def _report_run(*, revision: int) -> PaperReportRun:
    return PaperReportRun(
        account_id="demo",
        trade_date=SIGNAL_TIME.date(),
        logical_run_key="demo:2026-06-22:uni-1",
        revision=revision,
        screen_run_id="screen-1",
        rebalance_run_id="reb-1",
        signal_time=SIGNAL_TIME,
        execution_date=TRADE_DATE,
        config_hash="cfg-1",
        universe_hash="uni-1",
        run_status="completed",
    )


def test_revision_reports_never_overwrite(tmp_path, repo: PaperRepository) -> None:
    seed_execution_orders(repo)
    lease = acquire_test_lease(repo, owner_id="executor")
    PaperExecutionEngine().execute_rebalance(
        repo,
        rebalance_run_id="reb-1",
        execution_date=TRADE_DATE,
        execution_time=EXECUTION_TIME,
        fencing_token=lease.token,
        owner_id="executor",
        snapshots={"600000": open_snapshot()},
    )
    repo.expire_lease_for_test("demo")

    reporter = PaperReportWriter(tmp_path)
    first = reporter.write(_report_run(revision=1), paper_repo=repo)
    second = reporter.write(_report_run(revision=2), paper_repo=repo)
    assert first != second
    assert first.parent.exists()
    assert second.parent.exists()
    latest = json.loads((second.parents[1] / "latest.json").read_text(encoding="utf-8"))
    assert latest["revision"] == 2
    assert (second.parent / "daily_summary.md").exists()
    assert (first.parent / "run_manifest.json").exists()


def test_run_manifest_contains_required_fields(tmp_path, repo: PaperRepository) -> None:
    seed_execution_orders(repo)
    reporter = PaperReportWriter(tmp_path)
    manifest_path = reporter.write(_report_run(revision=1), paper_repo=repo)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["account_id"] == "demo"
    assert manifest["revision"] == 1
    assert manifest["screen_run_id"] == "screen-1"
    assert manifest["rebalance_run_id"] == "reb-1"
    assert manifest["manifest_hash"]
    assert "report_hashes" in manifest


def test_orders_csv_reflects_repo_state(tmp_path, repo: PaperRepository) -> None:
    seed_execution_orders(repo)
    reporter = PaperReportWriter(tmp_path)
    manifest_path = reporter.write(_report_run(revision=1), paper_repo=repo)
    orders_csv = (manifest_path.parent / "orders.csv").read_text(encoding="utf-8")
    assert "ord-buy-600000" in orders_csv
    assert "600000" in orders_csv
    assert str(Decimal("10.00")) in orders_csv or "10.00" in orders_csv
