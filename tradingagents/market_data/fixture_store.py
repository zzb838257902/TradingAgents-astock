"""Load deterministic fixtures into MarketDataRepository."""

from __future__ import annotations

from datetime import date, datetime, time

from tradingagents.backtest.limits import compute_limit_prices
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


def load_fixture_into_repository(repo: MarketDataRepository, fixture: dict) -> None:
    repo.upsert_security_records(_build_security_records(fixture))
    repo.upsert_daily_bars(_build_daily_bars(fixture))

    financials = []
    for row in fixture.get("financials", []):
        financials.append({
            "symbol": row["symbol"],
            "report_period": row["report_period"],
            "roe": row["roe"],
            "operating_cashflow": row["operating_cashflow"],
            "net_profit": row["net_profit"],
            "debt_ratio": row["debt_ratio"],
            "available_at": _parse_available_at(row["available_at"]),
            "source": "fixture",
        })
    repo.upsert_financials(financials)
    _load_auxiliary_from_fixture(repo, fixture)


def load_fixture_as_published(repo: MarketDataRepository, fixture: dict) -> None:
    securities = _build_security_records(fixture)
    sec_run = repo.begin_ingestion_run("security_master", {"fixture": True})
    repo.upsert_staging_securities(sec_run, securities)
    repo.publish_dataset_version(sec_run)

    daily_run = repo.begin_ingestion_run("daily_bars", {"fixture": True})
    repo.upsert_staging_daily_bars(daily_run, _build_daily_bars(fixture))
    repo.publish_dataset_version(daily_run)

    financials = []
    for row in fixture.get("financials", []):
        financials.append({
            "symbol": row["symbol"],
            "report_period": row["report_period"],
            "roe": row["roe"],
            "operating_cashflow": row["operating_cashflow"],
            "net_profit": row["net_profit"],
            "debt_ratio": row["debt_ratio"],
            "available_at": _parse_available_at(row["available_at"]),
            "source": "fixture",
        })
    repo.upsert_financials(financials)
    _load_auxiliary_from_fixture(repo, fixture)
