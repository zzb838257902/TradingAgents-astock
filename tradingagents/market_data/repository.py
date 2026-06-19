from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import duckdb

from tradingagents.market_data.contracts import SecurityRecord


class MarketDataRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        self._migrate()

    def _migrate(self) -> None:
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS securities (
                symbol VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                board VARCHAR NOT NULL,
                valid_from DATE NOT NULL,
                valid_to DATE,
                list_date DATE NOT NULL,
                delist_date DATE,
                status VARCHAR NOT NULL,
                st_flag BOOLEAN NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(symbol, valid_from)
            );
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume DOUBLE NOT NULL,
                amount DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(symbol, trade_date, source)
            );
            CREATE TABLE IF NOT EXISTS financials (
                symbol VARCHAR NOT NULL,
                report_period VARCHAR NOT NULL,
                roe DOUBLE NOT NULL,
                operating_cashflow DOUBLE NOT NULL,
                net_profit DOUBLE NOT NULL,
                debt_ratio DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(symbol, report_period, source)
            );
        """)

    def upsert_security_records(self, records: Iterable[SecurityRecord]) -> None:
        rows = [tuple(record.model_dump().values()) for record in records]
        self.connection.executemany(
            "INSERT OR REPLACE INTO securities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        self.connection.executemany(
            """INSERT OR REPLACE INTO daily_bars
               (symbol, trade_date, open, high, low, close, volume, amount,
                available_at, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            SELECT symbol, trade_date, open, high, low, close, volume, amount,
                   available_at, source
            FROM daily_bars
            WHERE symbol IN ({placeholders})
              AND trade_date <= ?
              AND available_at <= ?
        """
        params.extend([end, available_before])
        if start is not None:
            query += " AND trade_date >= ?"
            params.append(start)
        query += " ORDER BY symbol, trade_date"
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
        self.connection.executemany(
            """INSERT OR REPLACE INTO financials
               (symbol, report_period, roe, operating_cashflow, net_profit,
                debt_ratio, available_at, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )

    def get_financials(
        self, symbols: list[str], available_before: datetime
    ) -> list[dict]:
        if not symbols:
            return []
        placeholders = ", ".join("?" for _ in symbols)
        query = f"""
            SELECT symbol, report_period, roe, operating_cashflow, net_profit,
                   debt_ratio, available_at, source
            FROM financials
            WHERE symbol IN ({placeholders}) AND available_at <= ?
            ORDER BY symbol, available_at DESC
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
