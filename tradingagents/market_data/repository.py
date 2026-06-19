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
    "debt_ratio", "available_at", "source",
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
            """SELECT symbol, name, board, valid_from, valid_to, list_date,
                      delist_date, status, st_flag, available_at, source
               FROM securities
               WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)
                 AND available_at <= ?
               ORDER BY symbol""",
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
        values = [
            (
                row["symbol"],
                row["report_period"],
                row["roe"],
                row["operating_cashflow"],
                row["net_profit"],
                row["debt_ratio"],
                row["available_at"],
                row["source"],
            )
            for row in rows
        ]
        if not values:
            return
        placeholders = ", ".join("?" for _ in _FINANCIAL_COLUMNS)
        self.connection.executemany(
            f"INSERT OR REPLACE INTO financials ({', '.join(_FINANCIAL_COLUMNS)}) "
            f"VALUES ({placeholders})",
            values,
        )

    def get_financials(
        self, symbols: list[str], available_before: datetime
    ) -> list[dict]:
        if not symbols:
            return []
        placeholders = ", ".join("?" for _ in symbols)
        query = f"""
            SELECT f.symbol, f.report_period, f.roe, f.operating_cashflow,
                   f.net_profit, f.debt_ratio, f.available_at, f.source
            FROM financials f
            LEFT JOIN dataset_versions v ON f.dataset_version_id = v.version_id
            WHERE f.symbol IN ({placeholders})
              AND f.available_at <= ?
              AND (f.dataset_version_id IS NULL OR v.status = 'PUBLISHED')
            ORDER BY f.symbol, f.available_at DESC
        """
        params = [*symbols, available_before]
        columns = [
            "symbol", "report_period", "roe", "operating_cashflow", "net_profit",
            "debt_ratio", "available_at", "source",
        ]
        rows = self.connection.execute(query, params).fetchall()
        latest: dict[str, dict] = {}
        for row in rows:
            record = dict(zip(columns, row))
            if record["symbol"] not in latest:
                latest[record["symbol"]] = record
        return list(latest.values())

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

    def publish_dataset_version(self, run_id: str) -> str:
        run = self.connection.execute(
            "SELECT dataset FROM ingestion_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown ingestion run {run_id}")
        dataset = run[0]
        version_id = str(uuid.uuid4())
        now = datetime.now(tz=SHANGHAI)
        staging_rows = self.connection.execute(
            """SELECT symbol, trade_date, open, high, low, close, volume, amount,
                      prev_close, available_at, source, ingested_at
               FROM staging_daily_bars WHERE run_id = ?""",
            [run_id],
        ).fetchall()
        content_hash = _hash_payload([list(row) for row in staging_rows])
        self.connection.execute(
            """INSERT INTO dataset_versions
               (version_id, dataset, status, published_at, ingestion_run_id, content_hash)
               VALUES (?, ?, 'STAGING', NULL, ?, ?)""",
            [version_id, dataset, run_id, content_hash],
        )
        if staging_rows:
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
        self.connection.execute("DELETE FROM staging_daily_bars WHERE run_id = ?", [run_id])
        return version_id

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
        self.connection.execute("DELETE FROM staging_daily_bars WHERE run_id = ?", [run_id])

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
