"""Atomic daily report generation for Stage 6A paper operations."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tradingagents.paper.contracts import PaperFill, PaperOrder
from tradingagents.paper.repository import PaperRepository
from tradingagents.paper.screening import STRATEGY_VERSION

SHANGHAI = ZoneInfo("Asia/Shanghai")
REPORT_FILES = (
    "daily_summary.md",
    "orders.csv",
    "fills.csv",
    "positions.csv",
    "nav.csv",
    "run_manifest.json",
)


@dataclass(frozen=True)
class PaperReportRun:
    account_id: str
    trade_date: date
    logical_run_key: str
    revision: int
    screen_run_id: str | None = None
    rebalance_run_id: str | None = None
    signal_time: datetime | None = None
    execution_date: date | None = None
    config_hash: str | None = None
    universe_hash: str | None = None
    strategy_version: str = STRATEGY_VERSION
    dataset_versions: dict[str, Any] = field(default_factory=dict)
    event_dataset_versions: dict[str, Any] = field(default_factory=dict)
    run_status: str | None = None
    step_statuses: dict[str, str] = field(default_factory=dict)
    degradation_notes: list[str] = field(default_factory=list)


def _resolve_code_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with open(tmp_path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _content_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _csv_rows(headers: list[str], rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def _format_decimal(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


def build_orders_csv(orders: list[PaperOrder]) -> str:
    return _csv_rows(
        [
            "order_id",
            "rebalance_run_id",
            "symbol",
            "side",
            "planned_quantity",
            "filled_quantity",
            "remaining_quantity",
            "reference_price_cny",
            "status",
            "rejection_code",
            "rejection_detail",
        ],
        [
            [
                order.order_id,
                order.rebalance_run_id,
                order.symbol,
                order.side.value,
                order.planned_quantity,
                order.filled_quantity,
                order.remaining_quantity,
                _format_decimal(order.reference_price_cny),
                order.status.value,
                order.rejection_code or "",
                order.rejection_detail or "",
            ]
            for order in orders
        ],
    )


def build_fills_csv(fills: list[PaperFill]) -> str:
    return _csv_rows(
        [
            "fill_id",
            "order_id",
            "symbol",
            "execution_date",
            "execution_time",
            "quantity",
            "price_cny",
            "commission_cny",
            "stamp_tax_cny",
            "other_fee_cny",
        ],
        [
            [
                fill.fill_id,
                fill.order_id,
                fill.symbol,
                fill.execution_date.isoformat(),
                fill.execution_time.isoformat(),
                fill.quantity,
                _format_decimal(fill.price_cny),
                _format_decimal(fill.commission_cny),
                _format_decimal(fill.stamp_tax_cny),
                _format_decimal(fill.other_fee_cny),
            ]
            for fill in fills
        ],
    )


def build_positions_csv(
    paper_repo: PaperRepository,
    *,
    account_id: str,
    as_of_date: date,
) -> str:
    snapshot = paper_repo.load_account_snapshot(account_id, as_of_date=as_of_date)
    return _csv_rows(
        [
            "symbol",
            "quantity",
            "available_quantity",
            "average_cost_cny",
            "market_value_cny",
        ],
        [
            [
                symbol,
                projection.quantity,
                projection.available_quantity,
                _format_decimal(projection.average_cost_cny),
                _format_decimal(projection.market_value_cny),
            ]
            for symbol, projection in sorted(snapshot.positions.items())
        ],
    )


def build_nav_csv(paper_repo: PaperRepository, *, account_id: str, trade_date: date) -> str:
    nav = paper_repo.get_nav_snapshot(account_id, trade_date)
    if nav is None:
        return _csv_rows(
            [
                "valuation_date",
                "cash_cny",
                "positions_value_cny",
                "total_equity_cny",
                "daily_return",
                "cumulative_return",
                "drawdown",
            ],
            [],
        )
    return _csv_rows(
        [
            "valuation_date",
            "cash_cny",
            "positions_value_cny",
            "total_equity_cny",
            "daily_return",
            "cumulative_return",
            "drawdown",
        ],
        [
            [
                nav.valuation_date.isoformat(),
                _format_decimal(nav.cash_cny),
                _format_decimal(nav.positions_value_cny),
                _format_decimal(nav.total_equity_cny),
                _format_decimal(nav.daily_return),
                _format_decimal(nav.cumulative_return),
                _format_decimal(nav.drawdown),
            ]
        ],
    )


def build_daily_summary(
    *,
    run: PaperReportRun,
    paper_repo: PaperRepository,
    orders: list[PaperOrder],
    fills: list[PaperFill],
) -> str:
    snapshot = paper_repo.load_account_snapshot(run.account_id, as_of_date=run.trade_date)
    nav = paper_repo.get_nav_snapshot(run.account_id, run.trade_date)
    screen_status = "n/a"
    target_weights: dict[str, Any] = {}
    if run.screen_run_id:
        frozen = paper_repo.get_frozen_screen_run(run.screen_run_id)
        screen_status = frozen.status
        target_weights = json.loads(frozen.target_weights_json)

    rejected = [order for order in orders if order.rejection_code]
    lines = [
        f"# Daily Summary — {run.account_id} — {run.trade_date.isoformat()}",
        "",
        "## Run",
        f"- logical_run_key: `{run.logical_run_key}`",
        f"- revision: `{run.revision}`",
        f"- run_status: `{run.run_status or 'unknown'}`",
        f"- screen_run_id: `{run.screen_run_id or ''}`",
        f"- rebalance_run_id: `{run.rebalance_run_id or ''}`",
        "",
        "## Data Status",
        f"- screen_status: `{screen_status}`",
        f"- dataset_versions: `{json.dumps(run.dataset_versions, sort_keys=True)}`",
        f"- event_dataset_versions: `{json.dumps(run.event_dataset_versions, sort_keys=True)}`",
        "",
        "## Target Weights",
        json.dumps(target_weights, ensure_ascii=False, indent=2, sort_keys=True),
        "",
        "## Execution",
        f"- planned_orders: `{len(orders)}`",
        f"- fills: `{len(fills)}`",
        f"- rejections: `{len(rejected)}`",
        "",
        "## Account",
        f"- cash_cny: `{_format_decimal(snapshot.cash_cny)}`",
        f"- positions: `{len(snapshot.positions)}`",
        "",
        "## NAV",
    ]
    if nav is None:
        lines.append("- valuation: `missing`")
    else:
        lines.extend(
            [
                f"- total_equity_cny: `{_format_decimal(nav.total_equity_cny)}`",
                f"- daily_return: `{_format_decimal(nav.daily_return)}`",
                f"- cumulative_return: `{_format_decimal(nav.cumulative_return)}`",
                f"- drawdown: `{_format_decimal(nav.drawdown)}`",
            ]
        )
    if rejected:
        lines.extend(["", "## Rejections"])
        for order in rejected:
            lines.append(
                f"- `{order.symbol}` `{order.order_id}`: "
                f"`{order.rejection_code}` {order.rejection_detail or ''}".rstrip()
            )
    if run.degradation_notes:
        lines.extend(["", "## Degradation"])
        lines.extend(f"- {note}" for note in run.degradation_notes)
    if run.step_statuses:
        lines.extend(["", "## Step Status"])
        for step_name, status in sorted(run.step_statuses.items()):
            lines.append(f"- `{step_name}`: `{status}`")
    lines.append("")
    return "\n".join(lines)


def build_run_manifest(
    *,
    run: PaperReportRun,
    report_hashes: dict[str, str],
) -> dict[str, Any]:
    manifest = {
        "account_id": run.account_id,
        "trade_date": run.trade_date.isoformat(),
        "logical_run_key": run.logical_run_key,
        "revision": run.revision,
        "code_commit": _resolve_code_commit(),
        "config_hash": run.config_hash,
        "strategy_version": run.strategy_version,
        "universe_hash": run.universe_hash,
        "dataset_versions": run.dataset_versions,
        "event_dataset_versions": run.event_dataset_versions,
        "screen_run_id": run.screen_run_id,
        "rebalance_run_id": run.rebalance_run_id,
        "signal_time": run.signal_time.isoformat() if run.signal_time else None,
        "execution_date": run.execution_date.isoformat() if run.execution_date else None,
        "run_status": run.run_status,
        "step_statuses": run.step_statuses,
        "report_hashes": report_hashes,
        "generated_at": datetime.now(tz=SHANGHAI).isoformat(),
    }
    manifest["manifest_hash"] = _content_hash(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, default=str)
    )
    return manifest


def build_report_run_from_rebalance(
    paper_repo: PaperRepository,
    rebalance_run_id: str,
    *,
    run_status: str | None = None,
    step_statuses: dict[str, str] | None = None,
    degradation_notes: list[str] | None = None,
) -> PaperReportRun:
    revision = paper_repo.get_rebalance_revision(rebalance_run_id)
    if revision is None:
        raise ValueError(f"rebalance run {rebalance_run_id} not found")
    frozen = paper_repo.get_frozen_screen_run(revision.screen_run_id)
    return PaperReportRun(
        account_id=revision.account_id,
        trade_date=revision.signal_date,
        logical_run_key=revision.logical_run_key,
        revision=revision.revision,
        screen_run_id=revision.screen_run_id,
        rebalance_run_id=rebalance_run_id,
        signal_time=revision.signal_time,
        execution_date=revision.execution_date,
        config_hash=revision.config_hash,
        universe_hash=revision.universe_hash,
        strategy_version=revision.strategy_version,
        dataset_versions=json.loads(frozen.dataset_versions_json),
        event_dataset_versions=json.loads(frozen.event_dataset_versions_json),
        run_status=run_status,
        step_statuses=step_statuses or {},
        degradation_notes=degradation_notes or [],
    )


class PaperReportWriter:
    def __init__(self, home_dir: Path) -> None:
        self.reports_root = home_dir.expanduser() / "reports" / "paper"

    def revision_dir(self, run: PaperReportRun) -> Path:
        return (
            self.reports_root
            / run.account_id
            / run.trade_date.isoformat()
            / run.logical_run_key
            / f"rev-{run.revision}"
        )

    def latest_pointer_path(self, run: PaperReportRun) -> Path:
        return (
            self.reports_root
            / run.account_id
            / run.trade_date.isoformat()
            / run.logical_run_key
            / "latest.json"
        )

    def write(self, run: PaperReportRun, *, paper_repo: PaperRepository) -> Path:
        orders: list[PaperOrder] = []
        fills: list[PaperFill] = []
        if run.rebalance_run_id:
            orders = paper_repo.list_orders_for_rebalance(run.rebalance_run_id)
            fills = paper_repo.list_fills(
                run.account_id,
                rebalance_run_id=run.rebalance_run_id,
            )

        daily_summary = build_daily_summary(
            run=run,
            paper_repo=paper_repo,
            orders=orders,
            fills=fills,
        )
        orders_csv = build_orders_csv(orders)
        fills_csv = build_fills_csv(fills)
        positions_csv = build_positions_csv(
            paper_repo,
            account_id=run.account_id,
            as_of_date=run.trade_date,
        )
        nav_csv = build_nav_csv(
            paper_repo,
            account_id=run.account_id,
            trade_date=run.trade_date,
        )
        report_hashes = {
            "daily_summary.md": _content_hash(daily_summary),
            "orders.csv": _content_hash(orders_csv),
            "fills.csv": _content_hash(fills_csv),
            "positions.csv": _content_hash(positions_csv),
            "nav.csv": _content_hash(nav_csv),
        }
        manifest = build_run_manifest(run=run, report_hashes=report_hashes)
        manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)

        revision_dir = self.revision_dir(run)
        if revision_dir.exists():
            raise FileExistsError(f"report revision already exists: {revision_dir}")

        tmp_dir = revision_dir.with_name(f".{revision_dir.name}.tmp")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)

        payloads = {
            "daily_summary.md": daily_summary,
            "orders.csv": orders_csv,
            "fills.csv": fills_csv,
            "positions.csv": positions_csv,
            "nav.csv": nav_csv,
            "run_manifest.json": manifest_json,
        }
        for filename, content in payloads.items():
            _atomic_write_text(tmp_dir / filename, content)

        os.replace(tmp_dir, revision_dir)

        latest_payload = json.dumps(
            {
                "revision": run.revision,
                "relative_path": f"rev-{run.revision}/run_manifest.json",
                "manifest_hash": manifest["manifest_hash"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        _atomic_write_text(self.latest_pointer_path(run), latest_payload)
        return revision_dir / "run_manifest.json"
