from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import duckdb

from tradingagents.events.contracts import EventSymbolLink, MarketEvent, stable_event_id
from tradingagents.market_data.contracts import PITLevel, SecurityRecord
from tradingagents.market_data.financials import normalize_financial_row, pick_latest_visible_financials
from tradingagents.market_data.migrations import apply_migrations

SHANGHAI = ZoneInfo("Asia/Shanghai")

_SECURITY_COLUMNS = (
    "symbol", "name", "board", "valid_from", "valid_to", "list_date",
    "delist_date", "status", "st_flag", "available_at", "source",
)
_DAILY_BAR_COLUMNS = (
    "symbol", "trade_date", "open", "high", "low", "close", "volume",
    "amount", "available_at", "source",
)
_FINANCIAL_COLUMNS = (
    "symbol", "report_period", "roe", "operating_cashflow", "net_profit",
    "debt_ratio", "announcement_date", "actual_announcement_time", "available_at",
    "update_flag", "source_version", "record_type", "source",
)
_MARKET_EVENT_COLUMNS = (
    "event_id", "event_type", "title", "summary", "published_at", "available_at",
    "source", "source_url", "source_record_id", "source_version", "content_hash",
    "pit_level", "sentiment", "severity", "announcement_date_source",
    "raw_snapshot_id", "ingestion_run_id", "quality_status", "supersedes_event_id",
    "ingested_at",
)


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_sync_window_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _sanitize_request_params(params: dict[str, Any]) -> dict[str, Any]:
    redacted = {}
    for key, value in params.items():
        lowered = key.lower()
        if any(token in lowered for token in ("token", "secret", "password", "key")):
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


class MarketDataRepository:
    def __init__(self, path: Path, snapshot_dir: Path | None = None):
        self.path = path
        self.snapshot_dir = snapshot_dir
        path.parent.mkdir(parents=True, exist_ok=True)
        apply_migrations(path)
        self.connection = duckdb.connect(str(path))

    def _migrate(self) -> None:
        apply_migrations(self.path)

    def upsert_security_records(self, records: Iterable[SecurityRecord]) -> None:
        rows = [
            (
                record.symbol,
                record.name,
                record.board,
                record.valid_from,
                record.valid_to,
                record.list_date,
                record.delist_date,
                record.status,
                record.st_flag,
                record.available_at,
                record.source,
            )
            for record in records
        ]
        if not rows:
            return
        placeholders = ", ".join("?" for _ in _SECURITY_COLUMNS)
        self.connection.executemany(
            f"INSERT OR REPLACE INTO securities ({', '.join(_SECURITY_COLUMNS)}) "
            f"VALUES ({placeholders})",
            rows,
        )

    def list_effective_symbols(
        self, as_of: date, available_before: datetime
    ) -> list[str]:
        return [record.symbol for record in self.get_effective_securities(as_of, available_before)]

    def get_effective_securities(
        self, as_of: date, available_before: datetime
    ) -> list[SecurityRecord]:
        rows = self.connection.execute(
            """SELECT s.symbol, s.name, s.board, s.valid_from, s.valid_to, s.list_date,
                      s.delist_date, s.status, s.st_flag, s.available_at, s.source
               FROM securities s
               LEFT JOIN dataset_versions v ON s.dataset_version_id = v.version_id
               WHERE s.valid_from <= ? AND (s.valid_to IS NULL OR s.valid_to > ?)
                 AND s.available_at <= ?
                 AND (s.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
               ORDER BY s.symbol""",
            [as_of, as_of, available_before],
        ).fetchall()
        return [
            SecurityRecord(
                symbol=row[0],
                name=row[1],
                board=row[2],
                valid_from=row[3],
                valid_to=row[4],
                list_date=row[5],
                delist_date=row[6],
                status=row[7],
                st_flag=row[8],
                available_at=row[9],
                source=row[10],
            )
            for row in rows
        ]

    def upsert_security_master_snapshot(
        self,
        snapshot_date: date,
        records: Iterable[SecurityRecord],
    ) -> None:
        now = datetime.now(tz=SHANGHAI)
        values = [
            (
                snapshot_date,
                record.symbol,
                record.name,
                record.board,
                record.list_date,
                record.delist_date,
                record.status,
                record.st_flag,
                record.available_at,
                record.source,
                now,
            )
            for record in records
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO security_master_snapshots
               (snapshot_date, symbol, name, board, list_date, delist_date, status,
                st_flag, available_at, source, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def list_security_snapshot_dates(self, through: date | None = None) -> list[date]:
        if through is None:
            rows = self.connection.execute(
                """SELECT DISTINCT snapshot_date
                   FROM security_master_snapshots
                   ORDER BY snapshot_date"""
            ).fetchall()
        else:
            rows = self.connection.execute(
                """SELECT DISTINCT snapshot_date
                   FROM security_master_snapshots
                   WHERE snapshot_date <= ?
                   ORDER BY snapshot_date""",
                [through],
            ).fetchall()
        return [row[0] for row in rows]

    def has_security_snapshot_on(self, snapshot_date: date) -> bool:
        row = self.connection.execute(
            """SELECT 1 FROM security_master_snapshots
               WHERE snapshot_date = ? LIMIT 1""",
            [snapshot_date],
        ).fetchone()
        return row is not None

    def get_latest_security_snapshot_on_or_before(self, as_of: date) -> date | None:
        row = self.connection.execute(
            """SELECT MAX(snapshot_date)
               FROM security_master_snapshots
               WHERE snapshot_date <= ?""",
            [as_of],
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def get_effective_securities_from_snapshot(
        self,
        snapshot_date: date,
        as_of: date,
        available_before: datetime,
    ) -> list[SecurityRecord]:
        rows = self.connection.execute(
            """SELECT symbol, name, board, list_date, delist_date, status,
                      st_flag, available_at, source
               FROM security_master_snapshots
               WHERE snapshot_date = ?
                 AND list_date <= ?
                 AND (delist_date IS NULL OR delist_date > ?)
                 AND available_at <= ?
               ORDER BY symbol""",
            [snapshot_date, as_of, as_of, available_before],
        ).fetchall()
        return [
            SecurityRecord(
                symbol=row[0],
                name=row[1],
                board=row[2],
                valid_from=row[3],
                valid_to=row[4],
                list_date=row[3],
                delist_date=row[4],
                status=row[5],
                st_flag=row[6],
                available_at=row[7],
                source=row[8],
            )
            for row in rows
        ]

    def screening_security_snapshot_error(self, as_of: date) -> str | None:
        """Return error message when formal historical screening lacks a daily snapshot."""
        today = datetime.now(tz=SHANGHAI).date()
        if as_of < today and not self.has_security_snapshot_on(as_of):
            return (
                f"formal historical screening requires security_master snapshot on {as_of.isoformat()}"
            )
        return None

    def get_effective_securities_for_screening(
        self, as_of: date, available_before: datetime
    ) -> list[SecurityRecord]:
        """Same-day live pool from securities; historical as_of from daily snapshot."""
        today = datetime.now(tz=SHANGHAI).date()
        if as_of < today:
            return self.get_effective_securities_from_snapshot(
                as_of, as_of, available_before
            )
        return self.get_effective_securities(as_of, available_before)

    def seed_security_snapshot_for_date(
        self,
        snapshot_date: date,
        available_before: datetime | None = None,
    ) -> None:
        """Persist securities-table effective pool as a daily snapshot (tests/sync helper)."""
        if available_before is None:
            available_before = datetime.combine(
                snapshot_date,
                datetime.min.time().replace(hour=15, minute=30),
                tzinfo=SHANGHAI,
            )
        records = self.get_effective_securities(snapshot_date, available_before)
        self.upsert_security_master_snapshot(snapshot_date, records)

    def upsert_daily_bars(self, bars: Iterable[dict]) -> None:
        rows = [
            (
                bar["symbol"],
                bar["trade_date"],
                bar["open"],
                bar["high"],
                bar["low"],
                bar["close"],
                bar["volume"],
                bar["amount"],
                bar["available_at"],
                bar["source"],
            )
            for bar in bars
        ]
        if not rows:
            return
        placeholders = ", ".join("?" for _ in _DAILY_BAR_COLUMNS)
        self.connection.executemany(
            f"INSERT OR REPLACE INTO daily_bars ({', '.join(_DAILY_BAR_COLUMNS)}) "
            f"VALUES ({placeholders})",
            rows,
        )

    def get_daily_bars(
        self,
        symbols: list[str],
        end: date,
        available_before: datetime,
        start: date | None = None,
    ) -> list[dict]:
        if not symbols:
            return []
        placeholders = ", ".join("?" for _ in symbols)
        params: list = list(symbols)
        query = f"""
            SELECT b.symbol, b.trade_date, b.open, b.high, b.low, b.close,
                   b.volume, b.amount, b.available_at, b.source
            FROM daily_bars b
            LEFT JOIN dataset_versions v ON b.dataset_version_id = v.version_id
            WHERE b.symbol IN ({placeholders})
              AND b.trade_date <= ?
              AND b.available_at <= ?
              AND (b.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
        """
        params.extend([end, available_before])
        if start is not None:
            query += " AND b.trade_date >= ?"
            params.append(start)
        query += " ORDER BY b.symbol, b.trade_date"
        columns = [
            "symbol", "trade_date", "open", "high", "low", "close",
            "volume", "amount", "available_at", "source",
        ]
        rows = self.connection.execute(query, params).fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def upsert_financials(self, rows: Iterable[dict]) -> None:
        open_dates = self.list_open_trade_dates()
        values = []
        for row in rows:
            normalized = normalize_financial_row(row, open_dates=open_dates)
            values.append((
                normalized["symbol"],
                normalized["report_period"],
                normalized["roe"],
                normalized["operating_cashflow"],
                normalized["net_profit"],
                normalized["debt_ratio"],
                normalized["announcement_date"],
                normalized["actual_announcement_time"],
                normalized["available_at"],
                normalized["update_flag"],
                normalized["source_version"],
                normalized["record_type"],
                normalized["source"],
                normalized.get("ingested_at", datetime.now(tz=SHANGHAI)),
                normalized.get("dataset_version_id"),
            ))
        if not values:
            return
        placeholders = ", ".join("?" for _ in range(15))
        self.connection.executemany(
            f"""INSERT OR REPLACE INTO financials
               ({', '.join(_FINANCIAL_COLUMNS)}, ingested_at, dataset_version_id)
               VALUES ({placeholders})""",
            values,
        )

    def list_open_trade_dates(self) -> list[date]:
        rows = self.connection.execute(
            """SELECT trade_date
               FROM trade_calendar
               WHERE is_open = TRUE
               ORDER BY trade_date"""
        ).fetchall()
        return [row[0] for row in rows]

    def get_financials(
        self, symbols: list[str], available_before: datetime
    ) -> list[dict]:
        if not symbols:
            return []
        placeholders = ", ".join("?" for _ in symbols)
        query = f"""
            SELECT f.symbol, f.report_period, f.roe, f.operating_cashflow,
                   f.net_profit, f.debt_ratio, f.announcement_date,
                   f.actual_announcement_time, f.available_at, f.update_flag,
                   f.source_version, f.record_type, f.source
            FROM financials f
            LEFT JOIN dataset_versions v ON f.dataset_version_id = v.version_id
            WHERE f.symbol IN ({placeholders})
              AND f.available_at <= ?
              AND (f.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
            ORDER BY f.symbol, f.report_period, f.available_at
        """
        params = [*symbols, available_before]
        columns = [
            "symbol", "report_period", "roe", "operating_cashflow", "net_profit",
            "debt_ratio", "announcement_date", "actual_announcement_time",
            "available_at", "update_flag", "source_version", "record_type", "source",
        ]
        rows = self.connection.execute(query, params).fetchall()
        records = [dict(zip(columns, row)) for row in rows]
        return pick_latest_visible_financials(records)

    def begin_ingestion_run(self, dataset: str, params: dict[str, Any]) -> str:
        run_id = str(uuid.uuid4())
        now = datetime.now(tz=SHANGHAI)
        self.connection.execute(
            """INSERT INTO ingestion_runs
               (run_id, dataset, params_json, cursor_json, status, started_at)
               VALUES (?, ?, ?, NULL, 'RUNNING', ?)""",
            [run_id, dataset, json.dumps(params, sort_keys=True), now],
        )
        return run_id

    def upsert_staging_securities(self, run_id: str, records: Iterable[SecurityRecord]) -> None:
        now = datetime.now(tz=SHANGHAI)
        rows = [
            (
                run_id,
                record.symbol,
                record.name,
                record.board,
                record.valid_from,
                record.valid_to,
                record.list_date,
                record.delist_date,
                record.status,
                record.st_flag,
                record.available_at,
                record.source,
                now,
            )
            for record in records
        ]
        if not rows:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO staging_securities
               (run_id, symbol, name, board, valid_from, valid_to, list_date,
                delist_date, status, st_flag, available_at, source, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def upsert_staging_trade_calendar(self, run_id: str, days: Iterable[dict]) -> None:
        now = datetime.now(tz=SHANGHAI)
        rows = [
            (
                run_id,
                day["exchange"],
                day["trade_date"],
                day["is_open"],
                day["available_at"],
                day["source"],
                now,
            )
            for day in days
        ]
        if not rows:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO staging_trade_calendar
               (run_id, exchange, trade_date, is_open, available_at, source, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def upsert_staging_daily_bars(self, run_id: str, bars: Iterable[dict]) -> None:
        now = datetime.now(tz=SHANGHAI)
        rows = [
            (
                run_id,
                bar["symbol"],
                bar["trade_date"],
                bar["open"],
                bar["high"],
                bar["low"],
                bar["close"],
                bar["volume"],
                bar["amount"],
                bar.get("prev_close"),
                bar["available_at"],
                bar["source"],
                bar.get("ingested_at", now),
            )
            for bar in bars
        ]
        if not rows:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO staging_daily_bars
               (run_id, symbol, trade_date, open, high, low, close, volume, amount,
                prev_close, available_at, source, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def upsert_staging_financials(self, run_id: str, rows: Iterable[dict]) -> None:
        now = datetime.now(tz=SHANGHAI)
        open_dates = self.list_open_trade_dates()
        values = []
        for row in rows:
            normalized = normalize_financial_row(row, open_dates=open_dates or None)
            values.append((
                run_id,
                normalized["symbol"],
                normalized["report_period"],
                normalized["roe"],
                normalized["operating_cashflow"],
                normalized["net_profit"],
                normalized["debt_ratio"],
                normalized["announcement_date"],
                normalized["actual_announcement_time"],
                normalized["available_at"],
                normalized["update_flag"],
                normalized["source_version"],
                normalized["record_type"],
                normalized["source"],
                normalized.get("ingested_at", now),
            ))
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO staging_financials
               (run_id, symbol, report_period, roe, operating_cashflow, net_profit,
                debt_ratio, announcement_date, actual_announcement_time, available_at,
                update_flag, source_version, record_type, source, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def upsert_staging_adjustment_factors(self, run_id: str, rows: Iterable[dict]) -> None:
        now = datetime.now(tz=SHANGHAI)
        values = [
            (
                run_id,
                row["symbol"],
                row["trade_date"],
                row["factor"],
                row["available_at"],
                row["source"],
                row.get("ingested_at", now),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO staging_adjustment_factors
               (run_id, symbol, trade_date, factor, available_at, source, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def upsert_staging_corporate_actions(self, run_id: str, rows: Iterable[dict]) -> None:
        now = datetime.now(tz=SHANGHAI)
        values = [
            (
                run_id,
                row["symbol"],
                row["ex_date"],
                row["action_type"],
                row.get("cash_div"),
                row.get("stock_div"),
                row.get("split_ratio"),
                row.get("rights_ratio"),
                row["available_at"],
                row["source"],
                row.get("ingested_at", now),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO staging_corporate_actions
               (run_id, symbol, ex_date, action_type, cash_div, stock_div,
                split_ratio, rights_ratio, available_at, source, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def upsert_staging_board_memberships(self, run_id: str, rows: Iterable[dict]) -> None:
        now = datetime.now(tz=SHANGHAI)
        values = [
            (
                run_id,
                row["board_type"],
                row["board_code"],
                row["symbol"],
                row["membership_mode"],
                row.get("effective_from"),
                row.get("effective_to"),
                row.get("snapshot_date"),
                row["available_at"],
                row["source"],
                row.get("ingested_at", now),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO staging_board_memberships
               (run_id, board_type, board_code, symbol, membership_mode,
                effective_from, effective_to, snapshot_date, available_at, source,
                ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def find_published_version_by_hash(
        self, dataset: str, content_hash: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT version_id, dataset, status, published_at, ingestion_run_id, content_hash
               FROM dataset_versions
               WHERE dataset = ? AND content_hash = ? AND status = 'PUBLISHED'
               ORDER BY published_at DESC
               LIMIT 1""",
            [dataset, content_hash],
        ).fetchone()
        if row is None:
            return None
        columns = [
            "version_id", "dataset", "status", "published_at",
            "ingestion_run_id", "content_hash",
        ]
        return dict(zip(columns, row))

    def publish_dataset_version(self, run_id: str) -> str:
        run = self.connection.execute(
            "SELECT dataset FROM ingestion_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown ingestion run {run_id}")
        dataset = run[0]
        content_hash = self._staging_content_hash(run_id, dataset)
        existing = self.find_published_version_by_hash(dataset, content_hash)
        if existing is not None:
            now = datetime.now(tz=SHANGHAI)
            self.connection.execute(
                """UPDATE ingestion_runs
                   SET status = 'PUBLISHED', finished_at = ?, error_summary = NULL
                   WHERE run_id = ?""",
                [now, run_id],
            )
            self._clear_staging_for_run(run_id)
            return existing["version_id"]

        version_id = str(uuid.uuid4())
        now = datetime.now(tz=SHANGHAI)
        self.connection.execute(
            """INSERT INTO dataset_versions
               (version_id, dataset, status, published_at, ingestion_run_id, content_hash)
               VALUES (?, ?, 'STAGING', NULL, ?, ?)""",
            [version_id, dataset, run_id, content_hash],
        )
        if dataset == "daily_bars":
            self._copy_staging_daily_bars(run_id, version_id)
        elif dataset == "security_master":
            self._copy_staging_securities(run_id, version_id)
        elif dataset == "trade_calendar":
            self._copy_staging_trade_calendar(run_id, version_id)
        elif dataset == "financials":
            self._copy_staging_financials(run_id, version_id)
        elif dataset == "adjustment_factors":
            self._copy_staging_adjustment_factors(run_id, version_id)
            self._copy_staging_corporate_actions(run_id, version_id)
        elif dataset in {"industry_members", "concept_members", "index_members"}:
            self._copy_staging_board_memberships(run_id, version_id)
        else:
            raise ValueError(f"unsupported dataset for publish: {dataset}")

        self.connection.execute(
            """UPDATE dataset_versions
               SET status = 'PUBLISHED', published_at = ?
               WHERE version_id = ?""",
            [now, version_id],
        )
        self.connection.execute(
            """UPDATE ingestion_runs
               SET status = 'PUBLISHED', finished_at = ?, error_summary = NULL
               WHERE run_id = ?""",
            [now, run_id],
        )
        self._clear_staging_for_run(run_id)
        return version_id

    def _staging_content_hash(self, run_id: str, dataset: str) -> str:
        if dataset == "daily_bars":
            rows = self.connection.execute(
                """SELECT symbol, trade_date, open, high, low, close, volume, amount,
                          prev_close, available_at, source
                   FROM staging_daily_bars WHERE run_id = ?
                   ORDER BY symbol, trade_date""",
                [run_id],
            ).fetchall()
        elif dataset == "security_master":
            rows = self.connection.execute(
                """SELECT symbol, name, board, valid_from, valid_to, list_date,
                          delist_date, status, st_flag, available_at, source
                   FROM staging_securities WHERE run_id = ?
                   ORDER BY symbol, valid_from""",
                [run_id],
            ).fetchall()
        elif dataset == "trade_calendar":
            rows = self.connection.execute(
                """SELECT exchange, trade_date, is_open, available_at, source
                   FROM staging_trade_calendar WHERE run_id = ?
                   ORDER BY exchange, trade_date""",
                [run_id],
            ).fetchall()
        elif dataset == "financials":
            rows = self.connection.execute(
                """SELECT symbol, report_period, roe, operating_cashflow, net_profit,
                          debt_ratio, announcement_date, actual_announcement_time,
                          available_at, update_flag, source_version, record_type, source
                   FROM staging_financials WHERE run_id = ?
                   ORDER BY symbol, report_period, announcement_date""",
                [run_id],
            ).fetchall()
        elif dataset == "adjustment_factors":
            factor_rows = self.connection.execute(
                """SELECT symbol, trade_date, factor, available_at, source
                   FROM staging_adjustment_factors WHERE run_id = ?
                   ORDER BY symbol, trade_date""",
                [run_id],
            ).fetchall()
            action_rows = self.connection.execute(
                """SELECT symbol, ex_date, action_type, cash_div, stock_div,
                          split_ratio, rights_ratio, available_at, source
                   FROM staging_corporate_actions WHERE run_id = ?
                   ORDER BY symbol, ex_date, action_type""",
                [run_id],
            ).fetchall()
            rows = factor_rows + [("actions", *row) for row in action_rows]
        elif dataset in {"industry_members", "concept_members", "index_members"}:
            rows = self.connection.execute(
                """SELECT board_type, board_code, symbol, membership_mode,
                          effective_from, effective_to, snapshot_date, available_at, source
                   FROM staging_board_memberships WHERE run_id = ?
                   ORDER BY board_type, board_code, symbol, effective_from""",
                [run_id],
            ).fetchall()
        else:
            rows = []
        return _hash_payload([list(row) for row in rows])

    def _copy_staging_daily_bars(self, run_id: str, version_id: str) -> None:
        staging_rows = self.connection.execute(
            """SELECT symbol, trade_date, open, high, low, close, volume, amount,
                      prev_close, available_at, source, ingested_at
               FROM staging_daily_bars WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if not staging_rows:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO daily_bars
               (symbol, trade_date, open, high, low, close, volume, amount,
                available_at, source, prev_close, ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                    row[9], row[10], row[8], row[11], version_id,
                )
                for row in staging_rows
            ],
        )

    def _copy_staging_securities(self, run_id: str, version_id: str) -> None:
        staging_rows = self.connection.execute(
            """SELECT symbol, name, board, valid_from, valid_to, list_date,
                      delist_date, status, st_flag, available_at, source, ingested_at
               FROM staging_securities WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if not staging_rows:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO securities
               (symbol, name, board, valid_from, valid_to, list_date,
                delist_date, status, st_flag, available_at, source,
                ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(*row[:11], row[11], version_id) for row in staging_rows],
        )

    def _copy_staging_trade_calendar(self, run_id: str, version_id: str) -> None:
        staging_rows = self.connection.execute(
            """SELECT exchange, trade_date, is_open, available_at, source, ingested_at
               FROM staging_trade_calendar WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if not staging_rows:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO trade_calendar
               (exchange, trade_date, is_open, available_at, source,
                ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (row[0], row[1], row[2], row[3], row[4], row[5], version_id)
                for row in staging_rows
            ],
        )

    def _copy_staging_financials(self, run_id: str, version_id: str) -> None:
        staging_rows = self.connection.execute(
            f"""SELECT {', '.join(_FINANCIAL_COLUMNS)}, ingested_at
               FROM staging_financials WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if not staging_rows:
            return
        self.connection.executemany(
            f"""INSERT OR REPLACE INTO financials
               ({', '.join(_FINANCIAL_COLUMNS)}, ingested_at, dataset_version_id)
               VALUES ({', '.join('?' for _ in range(15))})""",
            [(*row[:13], row[13], version_id) for row in staging_rows],
        )

    def _copy_staging_adjustment_factors(self, run_id: str, version_id: str) -> None:
        staging_rows = self.connection.execute(
            """SELECT symbol, trade_date, factor, available_at, source, ingested_at
               FROM staging_adjustment_factors WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if not staging_rows:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO adjustment_factors
               (symbol, trade_date, factor, available_at, source, ingested_at,
                dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(*row[:6], version_id) for row in staging_rows],
        )

    def _copy_staging_corporate_actions(self, run_id: str, version_id: str) -> None:
        staging_rows = self.connection.execute(
            """SELECT symbol, ex_date, action_type, cash_div, stock_div, split_ratio,
                      rights_ratio, available_at, source, ingested_at
               FROM staging_corporate_actions WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if not staging_rows:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO corporate_actions
               (symbol, ex_date, action_type, cash_div, stock_div, split_ratio,
                rights_ratio, available_at, source, ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(*row[:10], version_id) for row in staging_rows],
        )

    def _copy_staging_board_memberships(self, run_id: str, version_id: str) -> None:
        staging_rows = self.connection.execute(
            """SELECT board_type, board_code, symbol, membership_mode, effective_from,
                      effective_to, snapshot_date, available_at, source, ingested_at
               FROM staging_board_memberships WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if not staging_rows:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO board_memberships
               (board_type, board_code, symbol, membership_mode, effective_from,
                effective_to, snapshot_date, available_at, source, ingested_at,
                dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(*row[:10], version_id) for row in staging_rows],
        )

    def _clear_staging_for_run(self, run_id: str) -> None:
        self.connection.execute("DELETE FROM staging_daily_bars WHERE run_id = ?", [run_id])
        self.connection.execute("DELETE FROM staging_securities WHERE run_id = ?", [run_id])
        self.connection.execute(
            "DELETE FROM staging_trade_calendar WHERE run_id = ?", [run_id]
        )
        self.connection.execute("DELETE FROM staging_financials WHERE run_id = ?", [run_id])
        self.connection.execute(
            "DELETE FROM staging_adjustment_factors WHERE run_id = ?", [run_id]
        )
        self.connection.execute(
            "DELETE FROM staging_corporate_actions WHERE run_id = ?", [run_id]
        )
        self.connection.execute(
            "DELETE FROM staging_board_memberships WHERE run_id = ?", [run_id]
        )
        self.connection.execute(
            "DELETE FROM staging_market_events WHERE run_id = ?", [run_id]
        )
        self.connection.execute(
            "DELETE FROM staging_event_symbol_links WHERE run_id = ?", [run_id]
        )
        self.connection.execute(
            "DELETE FROM staging_event_tags WHERE run_id = ?", [run_id]
        )

    def _market_event_row(self, event: MarketEvent, *, run_id: str | None = None) -> tuple:
        ingested = event.ingested_at or datetime.now(tz=SHANGHAI)
        return (
            *((run_id,) if run_id is not None else ()),
            event.event_id,
            event.event_type.value,
            event.title,
            event.summary,
            event.published_at,
            event.available_at,
            event.source,
            event.source_url,
            event.source_record_id,
            event.source_version,
            event.content_hash,
            event.pit_level.value,
            event.sentiment.value,
            event.severity.value,
            event.announcement_date_source.value if event.announcement_date_source else None,
            event.raw_snapshot_id,
            event.ingestion_run_id or run_id,
            event.quality_status.value,
            event.supersedes_event_id,
            ingested,
        )

    def _staging_event_stable_keys(self, run_id: str) -> set[str]:
        rows = self.connection.execute(
            """SELECT source, source_record_id, source_version
               FROM staging_market_events WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        return {
            f"{row[0]}:{row[1]}:{row[2] or 'v0'}"
            for row in rows
        }

    def upsert_staging_event_bundle(
        self,
        run_id: str,
        *,
        events: list[MarketEvent],
        links: list[EventSymbolLink],
        tags: list[dict[str, str]],
    ) -> None:
        run = self.connection.execute(
            "SELECT dataset FROM ingestion_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown ingestion run {run_id}")
        if run[0] != "market_events":
            raise ValueError(f"run {run_id} is not a market_events ingestion run")

        existing_keys = self._staging_event_stable_keys(run_id)
        batch_keys: set[str] = set()
        event_ids = {event.event_id for event in events}
        for event in events:
            key = stable_event_id(event)
            if key in batch_keys or key in existing_keys:
                raise ValueError("duplicate stable event key")
            batch_keys.add(key)

        for link in links:
            if link.event_id not in event_ids:
                raise ValueError(f"link references unknown event_id {link.event_id}")
        for tag in tags:
            if tag["event_id"] not in event_ids:
                raise ValueError(f"tag references unknown event_id {tag['event_id']}")

        if events:
            placeholders = ", ".join("?" for _ in range(len(_MARKET_EVENT_COLUMNS) + 1))
            columns = ", ".join(("run_id", *_MARKET_EVENT_COLUMNS))
            self.connection.executemany(
                f"""INSERT INTO staging_market_events ({columns})
                    VALUES ({placeholders})""",
                [self._market_event_row(event, run_id=run_id) for event in events],
            )
        if links:
            self.connection.executemany(
                """INSERT INTO staging_event_symbol_links
                   (run_id, event_id, symbol, role, available_at, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (run_id, link.event_id, link.symbol, link.role, link.available_at, link.source)
                    for link in links
                ],
            )
        if tags:
            self.connection.executemany(
                """INSERT INTO staging_event_tags
                   (run_id, event_id, tag_key, tag_value)
                   VALUES (?, ?, ?, ?)""",
                [
                    (run_id, tag["event_id"], tag["tag_key"], tag["tag_value"])
                    for tag in tags
                ],
            )

    def _validate_staging_event_bundle(self, run_id: str) -> list[str]:
        rows = self.connection.execute(
            """SELECT event_id, pit_level, available_at, quality_status
                FROM staging_market_events WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        errors: list[str] = []
        for event_id, pit_level, available_at, _quality in rows:
            if pit_level == PITLevel.PIT_REQUIRED.value and available_at is None:
                errors.append(f"{event_id}: pit_required event requires available_at")
        link_rows = self.connection.execute(
            """SELECT event_id FROM staging_event_symbol_links WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        event_ids = {row[0] for row in rows}
        for (link_event_id,) in link_rows:
            if link_event_id not in event_ids:
                errors.append(f"link references missing event {link_event_id}")
        tag_rows = self.connection.execute(
            """SELECT event_id FROM staging_event_tags WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        for (tag_event_id,) in tag_rows:
            if tag_event_id not in event_ids:
                errors.append(f"tag references missing event {tag_event_id}")
        return errors

    def _staging_event_bundle_hash(self, run_id: str) -> str:
        event_rows = self.connection.execute(
            f"""SELECT {', '.join(_MARKET_EVENT_COLUMNS)}
                FROM staging_market_events WHERE run_id = ?
                ORDER BY event_id""",
            [run_id],
        ).fetchall()
        link_rows = self.connection.execute(
            """SELECT event_id, symbol, role, available_at, source
               FROM staging_event_symbol_links WHERE run_id = ?
               ORDER BY event_id, symbol, role""",
            [run_id],
        ).fetchall()
        tag_rows = self.connection.execute(
            """SELECT event_id, tag_key, tag_value
               FROM staging_event_tags WHERE run_id = ?
               ORDER BY event_id, tag_key""",
            [run_id],
        ).fetchall()
        payload: dict[str, Any] = {
            "events": [list(row) for row in event_rows],
            "links": [list(row) for row in link_rows],
            "tags": [list(row) for row in tag_rows],
        }
        if not event_rows:
            row = self.connection.execute(
                "SELECT params_json FROM ingestion_runs WHERE run_id = ?",
                [run_id],
            ).fetchone()
            if row is not None:
                payload["params"] = json.loads(row[0])
        return _hash_payload(payload)

    def _copy_staging_event_bundle(self, run_id: str, version_id: str) -> None:
        event_rows = self.connection.execute(
            f"""SELECT {', '.join(_MARKET_EVENT_COLUMNS)}
                FROM staging_market_events WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if event_rows:
            self.connection.executemany(
                f"""INSERT OR REPLACE INTO market_events
                    ({', '.join(_MARKET_EVENT_COLUMNS)}, dataset_version_id)
                    VALUES ({', '.join('?' for _ in range(21))})""",
                [(*row, version_id) for row in event_rows],
            )
        link_rows = self.connection.execute(
            """SELECT event_id, symbol, role, available_at, source
               FROM staging_event_symbol_links WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if link_rows:
            self.connection.executemany(
                """INSERT OR REPLACE INTO event_symbol_links
                   (event_id, symbol, role, available_at, source, dataset_version_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(*row, version_id) for row in link_rows],
            )
        tag_rows = self.connection.execute(
            """SELECT event_id, tag_key, tag_value
               FROM staging_event_tags WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        if tag_rows:
            self.connection.executemany(
                """INSERT OR REPLACE INTO event_tags
                   (event_id, tag_key, tag_value, dataset_version_id)
                   VALUES (?, ?, ?, ?)""",
                [(*row, version_id) for row in tag_rows],
            )

    def publish_event_bundle(self, run_id: str) -> str:
        run = self.connection.execute(
            "SELECT dataset FROM ingestion_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown ingestion run {run_id}")
        if run[0] != "market_events":
            raise ValueError(f"run {run_id} is not a market_events ingestion run")

        errors = self._validate_staging_event_bundle(run_id)
        if errors:
            summary = "; ".join(errors)
            self.mark_ingestion_failed(run_id, summary)
            raise ValueError(f"event bundle quality gate failed: {summary}")

        content_hash = self._staging_event_bundle_hash(run_id)
        existing = self.find_published_version_by_hash("market_events", content_hash)
        if existing is not None:
            now = datetime.now(tz=SHANGHAI)
            self.connection.execute(
                """UPDATE ingestion_runs
                   SET status = 'PUBLISHED', finished_at = ?, error_summary = NULL
                   WHERE run_id = ?""",
                [now, run_id],
            )
            self._clear_staging_for_run(run_id)
            return existing["version_id"]

        version_id = str(uuid.uuid4())
        now = datetime.now(tz=SHANGHAI)
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.connection.execute(
                """INSERT INTO dataset_versions
                   (version_id, dataset, status, published_at, ingestion_run_id, content_hash)
                   VALUES (?, ?, 'STAGING', NULL, ?, ?)""",
                [version_id, "market_events", run_id, content_hash],
            )
            self._copy_staging_event_bundle(run_id, version_id)
            self.connection.execute(
                """UPDATE dataset_versions
                   SET status = 'PUBLISHED', published_at = ?
                   WHERE version_id = ?""",
                [now, version_id],
            )
            self.connection.execute(
                """UPDATE ingestion_runs
                   SET status = 'PUBLISHED', finished_at = ?, error_summary = NULL
                   WHERE run_id = ?""",
                [now, run_id],
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        self._clear_staging_for_run(run_id)
        return version_id

    def get_market_events(
        self,
        symbols: list[str],
        available_before: datetime,
    ) -> list[dict[str, Any]]:
        if not symbols:
            return []
        placeholders = ", ".join("?" for _ in symbols)
        rows = self.connection.execute(
            f"""SELECT DISTINCT e.event_id, e.event_type, e.title, e.summary,
                       e.published_at, e.available_at, e.source, e.source_url,
                       e.source_record_id, e.source_version, e.content_hash,
                       e.pit_level, e.sentiment, e.severity, e.announcement_date_source,
                       e.quality_status, e.supersedes_event_id
                FROM market_events e
                INNER JOIN event_symbol_links l ON e.event_id = l.event_id
                INNER JOIN dataset_versions v ON e.dataset_version_id = v.version_id
                INNER JOIN dataset_versions lv ON l.dataset_version_id = lv.version_id
                WHERE l.symbol IN ({placeholders})
                  AND e.available_at IS NOT NULL
                  AND e.available_at <= ?
                  AND e.quality_status != 'rejected'
                  AND v.status = 'PUBLISHED'
                  AND lv.status = 'PUBLISHED'
                ORDER BY e.available_at, e.event_id""",
            [*symbols, available_before],
        ).fetchall()
        columns = [
            "event_id", "event_type", "title", "summary", "published_at", "available_at",
            "source", "source_url", "source_record_id", "source_version", "content_hash",
            "pit_level", "sentiment", "severity", "announcement_date_source",
            "quality_status", "supersedes_event_id",
        ]
        return [dict(zip(columns, row)) for row in rows]

    def get_event_tags(self, event_ids: list[str]) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        placeholders = ", ".join("?" for _ in event_ids)
        rows = self.connection.execute(
            f"""SELECT t.event_id, t.tag_key, t.tag_value
                FROM event_tags t
                INNER JOIN dataset_versions v ON t.dataset_version_id = v.version_id
                WHERE t.event_id IN ({placeholders})
                  AND v.status = 'PUBLISHED'
                ORDER BY t.event_id, t.tag_key""",
            event_ids,
        ).fetchall()
        columns = ["event_id", "tag_key", "tag_value"]
        return [dict(zip(columns, row)) for row in rows]

    def upsert_board_aliases(self, rows: Iterable[dict]) -> None:
        values = [
            (
                row["board_type"],
                row["board_code"],
                row["alias"],
                row["alias_normalized"],
                row["source"],
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO board_aliases
               (board_type, board_code, alias, alias_normalized, source)
               VALUES (?, ?, ?, ?, ?)""",
            values,
        )

    def lookup_board_aliases(self, alias_normalized: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT board_type, board_code, alias, alias_normalized, source
               FROM board_aliases
               WHERE alias_normalized = ?
               ORDER BY board_type, board_code""",
            [alias_normalized],
        ).fetchall()
        columns = ["board_type", "board_code", "alias", "alias_normalized", "source"]
        return [dict(zip(columns, row)) for row in rows]

    def list_board_definitions(self, board_type: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT board_type, board_code, name, pit_level, source, available_at
               FROM board_definitions
               WHERE board_type = ?
               ORDER BY board_code""",
            [board_type],
        ).fetchall()
        columns = [
            "board_type", "board_code", "name", "pit_level", "source", "available_at",
        ]
        return [dict(zip(columns, row)) for row in rows]

    def find_boards_by_exact_name(
        self, board_type: str, name: str
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT board_type, board_code, name, pit_level, source, available_at
               FROM board_definitions
               WHERE board_type = ? AND name = ?
               ORDER BY board_code""",
            [board_type, name],
        ).fetchall()
        columns = [
            "board_type", "board_code", "name", "pit_level", "source", "available_at",
        ]
        return [dict(zip(columns, row)) for row in rows]

    def search_board_candidates(
        self, board_type: str, query: str
    ) -> list[dict[str, Any]]:
        needle = f"%{query.strip()}%"
        rows = self.connection.execute(
            """SELECT DISTINCT d.board_type, d.board_code, d.name, d.pit_level,
                      d.source, d.available_at
               FROM board_definitions d
               LEFT JOIN board_aliases a
                 ON d.board_type = a.board_type AND d.board_code = a.board_code
               WHERE d.board_type = ?
                 AND (d.name LIKE ? OR a.alias LIKE ? OR a.alias_normalized LIKE ?)
               ORDER BY d.board_code""",
            [board_type, needle, needle, needle.lower()],
        ).fetchall()
        columns = [
            "board_type", "board_code", "name", "pit_level", "source", "available_at",
        ]
        return [dict(zip(columns, row)) for row in rows]

    def mark_ingestion_failed(self, run_id: str, error_summary: str) -> None:
        now = datetime.now(tz=SHANGHAI)
        self.connection.execute(
            """UPDATE ingestion_runs
               SET status = 'FAILED', finished_at = ?, error_summary = ?
               WHERE run_id = ?""",
            [now, error_summary, run_id],
        )
        self.connection.execute(
            """UPDATE dataset_versions
               SET status = 'FAILED'
               WHERE ingestion_run_id = ? AND status = 'STAGING'""",
            [run_id],
        )
        self._clear_staging_for_run(run_id)

    def get_trade_calendar(
        self,
        exchange: str,
        start: date,
        end: date,
        available_before: datetime,
    ) -> list[dict]:
        rows = self.connection.execute(
            """SELECT t.exchange, t.trade_date, t.is_open, t.available_at, t.source
               FROM trade_calendar t
               LEFT JOIN dataset_versions v ON t.dataset_version_id = v.version_id
               WHERE t.exchange = ?
                 AND t.trade_date >= ?
                 AND t.trade_date <= ?
                 AND t.available_at <= ?
                 AND (t.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
               ORDER BY t.trade_date""",
            [exchange, start, end, available_before],
        ).fetchall()
        columns = ["exchange", "trade_date", "is_open", "available_at", "source"]
        return [dict(zip(columns, row)) for row in rows]

    def count_effective_securities(self, as_of: date, available_before: datetime) -> int:
        return len(self.get_effective_securities(as_of, available_before))

    def count_tradable_securities(self, as_of: date, available_before: datetime) -> int:
        return sum(
            1
            for record in self.get_effective_securities(as_of, available_before)
            if not self.is_suspended_on(record.symbol, as_of, available_before)
        )

    def get_symbol_industry_labels(
        self,
        symbols: list[str],
        as_of: date,
        available_before: datetime,
    ) -> dict[str, str]:
        if not symbols:
            return {}
        from tradingagents.market_data.contracts import Membership, MembershipMode

        placeholders = ", ".join("?" for _ in symbols)
        rows = self.connection.execute(
            f"""SELECT m.symbol, d.name, m.membership_mode, m.effective_from,
                       m.effective_to, m.snapshot_date, m.available_at, m.source,
                       m.board_type, m.board_code
                FROM board_memberships m
                JOIN board_definitions d
                  ON m.board_type = d.board_type AND m.board_code = d.board_code
                LEFT JOIN dataset_versions v ON m.dataset_version_id = v.version_id
                WHERE m.board_type = 'industry'
                  AND m.symbol IN ({placeholders})
                  AND m.available_at <= ?
                  AND (m.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
                ORDER BY m.symbol, m.effective_from DESC""",
            [*symbols, available_before],
        ).fetchall()
        labels: dict[str, str] = {}
        for row in rows:
            membership = Membership(
                board_type=row[8],
                board_code=row[9],
                symbol=row[0],
                membership_mode=MembershipMode(row[2]),
                effective_from=row[3],
                effective_to=row[4],
                snapshot_date=row[5],
                available_at=row[6],
                source=row[7],
            )
            if membership.pit_member_on(as_of) and row[0] not in labels:
                labels[row[0]] = row[1]
        return labels

    def count_published_daily_symbols(self, trade_date: date) -> int:
        row = self.connection.execute(
            """SELECT COUNT(DISTINCT b.symbol)
               FROM daily_bars b
               LEFT JOIN dataset_versions v ON b.dataset_version_id = v.version_id
               WHERE b.trade_date = ?
                 AND (b.dataset_version_id IS NULL OR v.status = 'PUBLISHED')""",
            [trade_date],
        ).fetchone()
        return int(row[0]) if row else 0

    def record_quality_event(
        self,
        dataset: str,
        rule: str,
        severity: str,
        version_id: str | None = None,
        numerator: float | None = None,
        denominator: float | None = None,
        detail_json: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO data_quality_events
               (event_id, dataset, version_id, rule, severity,
                numerator, denominator, detail_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                str(uuid.uuid4()),
                dataset,
                version_id,
                rule,
                severity,
                numerator,
                denominator,
                json.dumps(detail_json or {}, ensure_ascii=False),
                datetime.now(tz=SHANGHAI),
            ],
        )

    def save_sync_state(self, key: str, payload: dict[str, Any]) -> None:
        now = datetime.now(tz=SHANGHAI)
        self.connection.execute(
            """INSERT OR REPLACE INTO sync_state(state_key, value_json, updated_at)
               VALUES (?, ?, ?)""",
            [key, json.dumps(payload, ensure_ascii=False, default=str), now],
        )

    def get_sync_state(self, key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT value_json FROM sync_state WHERE state_key = ?",
            [key],
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def get_capability_probe(self) -> dict[str, Any] | None:
        return self.get_sync_state("capability_probe")

    def get_latest_published_version(self, dataset: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT version_id, dataset, status, published_at, ingestion_run_id, content_hash
               FROM dataset_versions
               WHERE dataset = ? AND status = 'PUBLISHED'
               ORDER BY published_at DESC
               LIMIT 1""",
            [dataset],
        ).fetchone()
        if row is None:
            return None
        columns = [
            "version_id", "dataset", "status", "published_at",
            "ingestion_run_id", "content_hash",
        ]
        return dict(zip(columns, row))

    def get_latest_market_events_ingestion_params(self) -> dict[str, Any] | None:
        version = self.get_latest_published_version("market_events")
        if version is None:
            return None
        row = self.connection.execute(
            "SELECT params_json FROM ingestion_runs WHERE run_id = ?",
            [version["ingestion_run_id"]],
        ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def find_success_empty_announcement_sync(
        self,
        *,
        symbols: list[str],
        signal_time: datetime,
        window_start: date,
        window_end: date,
    ) -> dict[str, Any] | None:
        if not symbols:
            return None
        required = {str(symbol) for symbol in symbols}
        rows = self.connection.execute(
            """SELECT v.version_id, v.dataset, v.status, v.published_at,
                      v.ingestion_run_id, v.content_hash, r.params_json
               FROM dataset_versions v
               INNER JOIN ingestion_runs r ON v.ingestion_run_id = r.run_id
               WHERE v.dataset = 'market_events'
                 AND v.status = 'PUBLISHED'
                 AND v.published_at <= ?
               ORDER BY v.published_at DESC""",
            [signal_time],
        ).fetchall()
        for row in rows:
            params = json.loads(row[6])
            if not params.get("success_empty"):
                continue
            synced = {str(symbol) for symbol in (params.get("symbols") or [])}
            if not required.issubset(synced):
                continue
            sync_start = _parse_sync_window_date(params.get("start"))
            sync_end = _parse_sync_window_date(params.get("end"))
            if sync_start is None or sync_end is None:
                continue
            if sync_start > window_start or sync_end < window_end:
                continue
            return {
                "version_id": row[0],
                "dataset": row[1],
                "status": row[2],
                "published_at": row[3],
                "ingestion_run_id": row[4],
                "content_hash": row[5],
            }
        return None

    def has_success_empty_announcement_sync(
        self,
        *,
        symbols: list[str],
        signal_time: datetime,
        window_start: date,
        window_end: date,
    ) -> bool:
        return self.find_success_empty_announcement_sync(
            symbols=symbols,
            signal_time=signal_time,
            window_start=window_start,
            window_end=window_end,
        ) is not None

    def save_raw_snapshot(
        self,
        source: str,
        endpoint: str,
        request_params: dict[str, Any],
        response_body: Any,
        api_version: str | None = None,
    ) -> str:
        if self.snapshot_dir is None:
            raise ValueError("snapshot_dir is required to save raw snapshots")
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_id = str(uuid.uuid4())
        sanitized = _sanitize_request_params(request_params)
        request_hash = _hash_payload(sanitized)
        response_hash = _hash_payload(response_body)
        ingested_at = datetime.now(tz=SHANGHAI)
        file_path = self.snapshot_dir / f"{snapshot_id}.json.gz"
        payload = {
            "request": sanitized,
            "response": response_body,
            "api_version": api_version,
            "ingested_at": ingested_at.isoformat(),
        }
        with gzip.open(file_path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        self.connection.execute(
            """INSERT INTO raw_snapshots
               (snapshot_id, source, endpoint, request_hash, response_hash,
                file_path, ingested_at, api_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                snapshot_id, source, endpoint, request_hash, response_hash,
                str(file_path), ingested_at, api_version,
            ],
        )
        return snapshot_id

    def get_raw_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT snapshot_id, source, endpoint, request_hash, response_hash,
                      file_path, ingested_at, api_version
               FROM raw_snapshots WHERE snapshot_id = ?""",
            [snapshot_id],
        ).fetchone()
        if row is None:
            return None
        columns = [
            "snapshot_id", "source", "endpoint", "request_hash", "response_hash",
            "file_path", "ingested_at", "api_version",
        ]
        return dict(zip(columns, row))

    def upsert_security_status_history(self, rows: Iterable[dict]) -> None:
        values = [
            (
                row["symbol"],
                row["status"],
                row["effective_from"],
                row.get("effective_to"),
                row["available_at"],
                row["source"],
                row.get("ingested_at", datetime.now(tz=SHANGHAI)),
                row.get("dataset_version_id"),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO security_status_history
               (symbol, status, effective_from, effective_to, available_at, source,
                ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def is_st_on(
        self,
        symbol: str,
        as_of: date,
        available_before: datetime,
        fallback: bool = False,
    ) -> bool:
        row = self.connection.execute(
            """SELECT 1
               FROM security_status_history s
               LEFT JOIN dataset_versions v ON s.dataset_version_id = v.version_id
               WHERE s.symbol = ?
                 AND s.effective_from <= ?
                 AND (s.effective_to IS NULL OR s.effective_to > ?)
                 AND s.available_at <= ?
                 AND (s.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
                 AND UPPER(s.status) LIKE 'ST%'
               LIMIT 1""",
            [symbol, as_of, as_of, available_before],
        ).fetchone()
        if row is not None:
            return True
        return fallback

    def upsert_name_history(self, rows: Iterable[dict]) -> None:
        values = [
            (
                row["symbol"],
                row["name"],
                row["effective_from"],
                row.get("effective_to"),
                row["available_at"],
                row["source"],
                row.get("ingested_at", datetime.now(tz=SHANGHAI)),
                row.get("dataset_version_id"),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO name_history
               (symbol, name, effective_from, effective_to, available_at, source,
                ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def upsert_suspension_events(self, rows: Iterable[dict]) -> None:
        values = [
            (
                row["symbol"],
                row["start_date"],
                row.get("end_date"),
                row.get("reason"),
                row["available_at"],
                row["source"],
                row.get("ingested_at", datetime.now(tz=SHANGHAI)),
                row.get("dataset_version_id"),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO suspension_events
               (symbol, start_date, end_date, reason, available_at, source,
                ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def is_suspended_on(
        self,
        symbol: str,
        trade_date: date,
        available_before: datetime,
    ) -> bool:
        row = self.connection.execute(
            """SELECT 1
               FROM suspension_events s
               LEFT JOIN dataset_versions v ON s.dataset_version_id = v.version_id
               WHERE s.symbol = ?
                 AND s.start_date <= ?
                 AND (s.end_date IS NULL OR s.end_date >= ?)
                 AND s.available_at <= ?
                 AND (s.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
               LIMIT 1""",
            [symbol, trade_date, trade_date, available_before],
        ).fetchone()
        return row is not None

    def upsert_adjustment_factors(self, rows: Iterable[dict]) -> None:
        values = [
            (
                row["symbol"],
                row["trade_date"],
                row["factor"],
                row["available_at"],
                row["source"],
                row.get("ingested_at", datetime.now(tz=SHANGHAI)),
                row.get("dataset_version_id"),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO adjustment_factors
               (symbol, trade_date, factor, available_at, source,
                ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def get_adjustment_factors(
        self,
        symbols: list[str],
        end: date,
        available_before: datetime,
        start: date | None = None,
    ) -> list[dict]:
        if not symbols:
            return []
        placeholders = ", ".join("?" for _ in symbols)
        params: list = list(symbols)
        query = f"""
            SELECT a.symbol, a.trade_date, a.factor, a.available_at, a.source
            FROM adjustment_factors a
            LEFT JOIN dataset_versions v ON a.dataset_version_id = v.version_id
            WHERE a.symbol IN ({placeholders})
              AND a.trade_date <= ?
              AND a.available_at <= ?
              AND (a.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
        """
        params.extend([end, available_before])
        if start is not None:
            query += " AND a.trade_date >= ?"
            params.append(start)
        query += " ORDER BY a.symbol, a.trade_date"
        columns = ["symbol", "trade_date", "factor", "available_at", "source"]
        rows = self.connection.execute(query, params).fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def upsert_corporate_actions(self, rows: Iterable[dict]) -> None:
        values = [
            (
                row["symbol"],
                row["ex_date"],
                row["action_type"],
                row.get("cash_div"),
                row.get("stock_div"),
                row.get("split_ratio"),
                row.get("rights_ratio"),
                row["available_at"],
                row["source"],
                row.get("ingested_at", datetime.now(tz=SHANGHAI)),
                row.get("dataset_version_id"),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO corporate_actions
               (symbol, ex_date, action_type, cash_div, stock_div, split_ratio,
                rights_ratio, available_at, source, ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def get_corporate_actions(
        self,
        symbols: list[str],
        end: date,
        available_before: datetime,
        start: date | None = None,
    ) -> list[dict]:
        if not symbols:
            return []
        placeholders = ", ".join("?" for _ in symbols)
        params: list = list(symbols)
        query = f"""
            SELECT a.symbol, a.ex_date, a.action_type, a.cash_div, a.stock_div,
                   a.split_ratio, a.rights_ratio, a.available_at, a.source
            FROM corporate_actions a
            LEFT JOIN dataset_versions v ON a.dataset_version_id = v.version_id
            WHERE a.symbol IN ({placeholders})
              AND a.ex_date <= ?
              AND a.available_at <= ?
              AND (a.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
        """
        params.extend([end, available_before])
        if start is not None:
            query += " AND a.ex_date >= ?"
            params.append(start)
        query += " ORDER BY a.symbol, a.ex_date"
        columns = [
            "symbol", "ex_date", "action_type", "cash_div", "stock_div",
            "split_ratio", "rights_ratio", "available_at", "source",
        ]
        rows = self.connection.execute(query, params).fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def upsert_price_limits(self, rows: Iterable[dict]) -> None:
        values = [
            (
                row["symbol"],
                row["trade_date"],
                row["limit_up"],
                row["limit_down"],
                row["available_at"],
                row["source"],
                row.get("ingested_at", datetime.now(tz=SHANGHAI)),
                row.get("dataset_version_id"),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO price_limits
               (symbol, trade_date, limit_up, limit_down, available_at, source,
                ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def upsert_board_definitions(self, rows: Iterable[dict]) -> None:
        values = [
            (
                row["board_type"],
                row["board_code"],
                row["name"],
                row["pit_level"],
                row["source"],
                row["available_at"],
                row.get("ingested_at", datetime.now(tz=SHANGHAI)),
                row.get("dataset_version_id"),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO board_definitions
               (board_type, board_code, name, pit_level, source, available_at,
                ingested_at, dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def get_board_definition(
        self, board_type: str, board_code: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT board_type, board_code, name, pit_level, source, available_at
               FROM board_definitions
               WHERE board_type = ? AND board_code = ?
               ORDER BY available_at DESC
               LIMIT 1""",
            [board_type, board_code],
        ).fetchone()
        if row is None:
            return None
        columns = [
            "board_type", "board_code", "name", "pit_level", "source", "available_at",
        ]
        return dict(zip(columns, row))

    def upsert_board_memberships(self, rows: Iterable[dict]) -> None:
        values = [
            (
                row["board_type"],
                row["board_code"],
                row["symbol"],
                row["membership_mode"],
                row.get("effective_from"),
                row.get("effective_to"),
                row.get("snapshot_date"),
                row["available_at"],
                row["source"],
                row.get("ingested_at", datetime.now(tz=SHANGHAI)),
                row.get("dataset_version_id"),
            )
            for row in rows
        ]
        if not values:
            return
        self.connection.executemany(
            """INSERT OR REPLACE INTO board_memberships
               (board_type, board_code, symbol, membership_mode, effective_from,
                effective_to, snapshot_date, available_at, source, ingested_at,
                dataset_version_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def get_board_memberships(
        self,
        board_type: str,
        board_code: str,
        as_of: date,
        available_before: datetime,
    ) -> list:
        from tradingagents.market_data.contracts import Membership, MembershipMode

        rows = self.connection.execute(
            """SELECT m.board_type, m.board_code, m.symbol, m.membership_mode,
                      m.effective_from, m.effective_to, m.snapshot_date,
                      m.available_at, m.source
               FROM board_memberships m
               LEFT JOIN dataset_versions v ON m.dataset_version_id = v.version_id
               WHERE m.board_type = ?
                 AND m.board_code = ?
                 AND m.available_at <= ?
                 AND (m.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
               ORDER BY m.symbol, m.effective_from""",
            [board_type, board_code, available_before],
        ).fetchall()
        memberships = [
            Membership(
                board_type=row[0],
                board_code=row[1],
                symbol=row[2],
                membership_mode=MembershipMode(row[3]),
                effective_from=row[4],
                effective_to=row[5],
                snapshot_date=row[6],
                available_at=row[7],
                source=row[8],
            )
            for row in rows
        ]
        return [item for item in memberships if item.pit_member_on(as_of)]

    def list_quality_events(self, dataset: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT event_id, dataset, version_id, rule, severity,
                      numerator, denominator, detail_json, created_at
               FROM data_quality_events
               WHERE dataset = ?
               ORDER BY created_at""",
            [dataset],
        ).fetchall()
        columns = [
            "event_id", "dataset", "version_id", "rule", "severity",
            "numerator", "denominator", "detail_json", "created_at",
        ]
        results = []
        for row in rows:
            item = dict(zip(columns, row))
            item["detail_json"] = json.loads(item["detail_json"])
            results.append(item)
        return results
