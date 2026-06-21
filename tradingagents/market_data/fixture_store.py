"""Load deterministic fixtures into MarketDataRepository."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from tradingagents.backtest.limits import compute_limit_prices
from tradingagents.market_data.adjustments import baseline_factor_row
from tradingagents.market_data.contracts import SecurityRecord
from tradingagents.market_data.market_hours import SHANGHAI, bar_available_at, ensure_aware_shanghai
from tradingagents.market_data.repository import MarketDataRepository


def _parse_available_at(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return ensure_aware_shanghai(value)
    return ensure_aware_shanghai(datetime.fromisoformat(value))


def _build_security_records(fixture: dict) -> list[SecurityRecord]:
    trading_dates = sorted(date.fromisoformat(key) for key in fixture["bars"])
    securities: list[SecurityRecord] = []
    for item in fixture["symbols"]:
        symbol = item["symbol"]
        list_date = date.fromisoformat(item["list_date"]) if item.get("list_date") else trading_dates[0]
        delist_date = None
        valid_to = None
        if item.get("delist_after"):
            delist_key = next(iter(fixture.get("delistings", {})), None)
            if delist_key:
                delist_date = date.fromisoformat(delist_key)
                valid_to = delist_date
        securities.append(SecurityRecord(
            symbol=symbol,
            name=item.get("name", symbol),
            board=item.get("board", "main"),
            valid_from=list_date,
            valid_to=valid_to,
            list_date=list_date,
            delist_date=delist_date,
            status="listed",
            st_flag=item.get("st_flag", False),
            available_at=datetime.combine(list_date, time(9, 0), tzinfo=SHANGHAI),
            source="fixture",
        ))
    return securities


def _build_daily_bars(fixture: dict) -> list[dict]:
    daily_bars = []
    prev_close: dict[str, float] = {}
    for trade_date_str in sorted(fixture["bars"]):
        trade_date = date.fromisoformat(trade_date_str)
        day_bars = fixture["bars"][trade_date_str]
        for symbol, bar in day_bars.items():
            daily_bars.append({
                "symbol": symbol,
                "trade_date": trade_date,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
                "amount": bar.get("amount", bar["close"] * bar["volume"]),
                "prev_close": prev_close.get(symbol, bar.get("open", bar["close"])),
                "available_at": bar_available_at(trade_date),
                "source": "fixture",
            })
            prev_close[symbol] = bar["close"]
    return daily_bars


def _load_auxiliary_from_fixture(repo: MarketDataRepository, fixture: dict) -> None:
    trading_dates = sorted(date.fromisoformat(key) for key in fixture["bars"])
    status_rows = []
    name_rows = []
    suspension_rows = []
    price_limit_rows = []
    prev_close: dict[str, float] = {}

    for item in fixture["symbols"]:
        symbol = item["symbol"]
        list_date = date.fromisoformat(item["list_date"]) if item.get("list_date") else trading_dates[0]
        available_at = datetime.combine(list_date, time(9, 0), tzinfo=SHANGHAI)
        name_rows.append({
            "symbol": symbol,
            "name": item.get("name", symbol),
            "effective_from": list_date,
            "effective_to": None,
            "available_at": available_at,
            "source": "fixture",
        })
        if item.get("st_flag"):
            status_rows.append({
                "symbol": symbol,
                "status": "ST",
                "effective_from": list_date,
                "effective_to": None,
                "available_at": available_at,
                "source": "fixture",
            })

    for trade_date_str in sorted(fixture["bars"]):
        trade_date = date.fromisoformat(trade_date_str)
        for symbol, bar in fixture["bars"][trade_date_str].items():
            meta = next(item for item in fixture["symbols"] if item["symbol"] == symbol)
            base_prev = prev_close.get(symbol, bar.get("open", bar["close"]))
            limit_up, limit_down = compute_limit_prices(
                base_prev,
                st_flag=meta.get("st_flag", False),
                board=meta.get("board", "main"),
            )
            price_limit_rows.append({
                "symbol": symbol,
                "trade_date": trade_date,
                "limit_up": limit_up,
                "limit_down": limit_down,
                "available_at": bar_available_at(trade_date),
                "source": "fixture",
            })
            if bar.get("suspended") or bar.get("volume", 0) <= 0:
                suspension_rows.append({
                    "symbol": symbol,
                    "start_date": trade_date,
                    "end_date": trade_date,
                    "reason": "suspended",
                    "available_at": bar_available_at(trade_date),
                    "source": "fixture",
                })
            prev_close[symbol] = bar["close"]

    repo.upsert_security_status_history(status_rows)
    repo.upsert_name_history(name_rows)
    repo.upsert_suspension_events(suspension_rows)
    repo.upsert_price_limits(price_limit_rows)


def _write_security_snapshots_for_fixture(
    repo: MarketDataRepository,
    fixture: dict,
    securities: list[SecurityRecord],
) -> None:
    trading_dates = sorted(date.fromisoformat(key) for key in fixture["bars"])
    for trade_date in trading_dates:
        effective = [
            record for record in securities
            if record.was_effective_on(trade_date)
        ]
        repo.upsert_security_master_snapshot(trade_date, effective)


def _load_adjustment_factors_from_fixture(repo: MarketDataRepository, fixture: dict) -> None:
    trading_dates = sorted(date.fromisoformat(key) for key in fixture["bars"])
    anchor_date = trading_dates[0]
    anchor_available = bar_available_at(anchor_date)
    rows = []
    for item in fixture.get("symbols", []):
        symbol = item["symbol"]
        rows.append(baseline_factor_row(
            symbol,
            anchor_date,
            available_at=anchor_available,
            source="fixture",
        ))
    for item in fixture.get("adjustment_factors", []):
        symbol = item["symbol"]
        rows.append({
            "symbol": symbol,
            "trade_date": date.fromisoformat(item["trade_date"])
            if isinstance(item["trade_date"], str) else item["trade_date"],
            "factor": float(item["factor"]),
            "available_at": _parse_available_at(item["available_at"]),
            "source": item.get("source", "fixture"),
        })
    if rows:
        repo.upsert_adjustment_factors(rows)


def _seed_fixture_trade_calendar(repo: MarketDataRepository, fixture: dict) -> None:
    trading_dates = sorted(date.fromisoformat(key) for key in fixture["bars"])
    rows_by_date: dict[date, dict] = {}
    if trading_dates:
        end = trading_dates[-1]
        earliest_list = min(
            date.fromisoformat(item["list_date"]) if item.get("list_date") else trading_dates[0]
            for item in fixture["symbols"]
        )
        start = min(earliest_list, trading_dates[0]) - timedelta(days=400)
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                rows_by_date[cursor] = {
                    "exchange": "SSE",
                    "trade_date": cursor,
                    "is_open": True,
                    "available_at": datetime.combine(cursor, time(9, 0), tzinfo=SHANGHAI),
                    "source": "fixture",
                }
            cursor += timedelta(days=1)
    for item in fixture.get("trade_calendar", []):
        trade_date = date.fromisoformat(item["trade_date"])
        rows_by_date[trade_date] = {
            "exchange": item.get("exchange", "SSE"),
            "trade_date": trade_date,
            "is_open": bool(item.get("is_open", True)),
            "available_at": datetime.combine(trade_date, time(9, 0), tzinfo=SHANGHAI),
            "source": "fixture",
        }
    rows = [rows_by_date[key] for key in sorted(rows_by_date)]
    if not rows:
        return
    run_id = repo.begin_ingestion_run("trade_calendar", {"fixture": True})
    repo.upsert_staging_trade_calendar(run_id, rows)
    repo.publish_dataset_version(run_id)


def load_fixture_into_repository(repo: MarketDataRepository, fixture: dict) -> None:
    securities = _build_security_records(fixture)
    repo.upsert_security_records(securities)
    _seed_fixture_trade_calendar(repo, fixture)
    repo.upsert_daily_bars(_build_daily_bars(fixture))
    _write_security_snapshots_for_fixture(repo, fixture, securities)
    _load_adjustment_factors_from_fixture(repo, fixture)

    financials = []
    for row in fixture.get("financials", []):
        item = {
            "symbol": row["symbol"],
            "report_period": row["report_period"],
            "roe": row["roe"],
            "operating_cashflow": row["operating_cashflow"],
            "net_profit": row["net_profit"],
            "debt_ratio": row["debt_ratio"],
            "source": "fixture",
            "update_flag": row.get("update_flag"),
            "source_version": row.get("source_version"),
            "record_type": row.get("record_type"),
        }
        if row.get("announcement_date"):
            item["announcement_date"] = date.fromisoformat(row["announcement_date"])
        if row.get("actual_announcement_time"):
            item["actual_announcement_time"] = _parse_available_at(row["actual_announcement_time"])
        if row.get("available_at"):
            item["available_at"] = _parse_available_at(row["available_at"])
        financials.append(item)
    repo.upsert_financials(financials)
    _load_board_data_from_fixture(repo, fixture)
    _load_auxiliary_from_fixture(repo, fixture)


def _load_board_data_from_fixture(repo: MarketDataRepository, fixture: dict) -> None:
    definitions = []
    for item in fixture.get("board_definitions", []):
        definitions.append({
            "board_type": item["board_type"],
            "board_code": item["board_code"],
            "name": item["name"],
            "pit_level": item["pit_level"],
            "source": "fixture",
            "available_at": _parse_available_at(
                item.get("available_at", "2020-01-01T09:00:00+08:00")
            ),
        })
    repo.upsert_board_definitions(definitions)

    memberships = []
    for item in fixture.get("board_memberships", []):
        row = {
            "board_type": item["board_type"],
            "board_code": item["board_code"],
            "symbol": item["symbol"],
            "membership_mode": item["membership_mode"],
            "effective_from": (
                date.fromisoformat(item["effective_from"])
                if item.get("effective_from") else None
            ),
            "effective_to": (
                date.fromisoformat(item["effective_to"])
                if item.get("effective_to") else None
            ),
            "snapshot_date": (
                date.fromisoformat(item["snapshot_date"])
                if item.get("snapshot_date") else None
            ),
            "available_at": _parse_available_at(item["available_at"]),
            "source": "fixture",
        }
        if item.get("membership_mode") == "dated_snapshot" and row["snapshot_date"]:
            row["effective_from"] = row["snapshot_date"]
            if row["effective_to"] is None:
                row["effective_to"] = row["snapshot_date"] + timedelta(days=1)
        memberships.append(row)
    repo.upsert_board_memberships(memberships)


def load_fixture_as_published(repo: MarketDataRepository, fixture: dict) -> None:
    securities = _build_security_records(fixture)
    sec_run = repo.begin_ingestion_run("security_master", {"fixture": True})
    repo.upsert_staging_securities(sec_run, securities)
    repo.publish_dataset_version(sec_run)
    _write_security_snapshots_for_fixture(repo, fixture, securities)
    _seed_fixture_trade_calendar(repo, fixture)

    daily_run = repo.begin_ingestion_run("daily_bars", {"fixture": True})
    repo.upsert_staging_daily_bars(daily_run, _build_daily_bars(fixture))
    repo.publish_dataset_version(daily_run)
    _load_adjustment_factors_from_fixture(repo, fixture)

    financials = []
    for row in fixture.get("financials", []):
        item = {
            "symbol": row["symbol"],
            "report_period": row["report_period"],
            "roe": row["roe"],
            "operating_cashflow": row["operating_cashflow"],
            "net_profit": row["net_profit"],
            "debt_ratio": row["debt_ratio"],
            "source": "fixture",
            "update_flag": row.get("update_flag"),
            "source_version": row.get("source_version"),
            "record_type": row.get("record_type"),
        }
        if row.get("announcement_date"):
            item["announcement_date"] = date.fromisoformat(row["announcement_date"])
        if row.get("actual_announcement_time"):
            item["actual_announcement_time"] = _parse_available_at(row["actual_announcement_time"])
        if row.get("available_at"):
            item["available_at"] = _parse_available_at(row["available_at"])
        financials.append(item)
    repo.upsert_financials(financials)
    _load_board_data_from_fixture(repo, fixture)
    _load_auxiliary_from_fixture(repo, fixture)
