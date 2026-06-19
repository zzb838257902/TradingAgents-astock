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

from tradingagents.market_data.contracts import SecurityRecord
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


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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

    def _clear_staging_for_run(self, run_id: str) -> None:
        self.connection.execute("DELETE FROM staging_daily_bars WHERE run_id = ?", [run_id])
        self.connection.execute("DELETE FROM staging_securities WHERE run_id = ?", [run_id])
        self.connection.execute(
            "DELETE FROM staging_trade_calendar WHERE run_id = ?", [run_id]
        )

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
