"""A-share daily price limit calculations."""

from __future__ import annotations

from datetime import date

from tradingagents.backtest.models import Bar


def limit_pct(st_flag: bool, board: str) -> float:
    if st_flag:
        return 0.05
    if board in {"gem", "star", "chinext", "科创板", "创业板"}:
        return 0.20
    return 0.10


def compute_limit_prices(
    prev_close: float, *, st_flag: bool = False, board: str = "main"
) -> tuple[float, float]:
    pct = limit_pct(st_flag, board)
    limit_up = round(prev_close * (1 + pct), 2)
    limit_down = round(prev_close * (1 - pct), 2)
    return limit_up, limit_down


def bar_from_dict(
    data: dict,
    *,
    prev_close: float | None,
    st_flag: bool = False,
    board: str = "main",
    strict: bool = True,
) -> Bar:
    if "limit_up" in data and "limit_down" in data:
        limit_up = data["limit_up"]
        limit_down = data["limit_down"]
    elif prev_close is not None:
        limit_up, limit_down = compute_limit_prices(prev_close, st_flag=st_flag, board=board)
    elif strict:
        raise ValueError("bar missing limit_up/limit_down and prev_close for strict backtest")
    else:
        raise ValueError("cannot infer limit prices")

    return Bar(
        open=data["open"],
        high=data["high"],
        low=data["low"],
        close=data["close"],
        volume=data["volume"],
        limit_up=limit_up,
        limit_down=limit_down,
        suspended=data.get("suspended", data["volume"] <= 0),
        prev_close=prev_close if prev_close is not None else data.get("prev_close"),
    )


def enrich_bars_with_limits(
    bars: dict[date, dict[str, dict]],
    symbols_meta: list[dict],
) -> dict[date, dict[str, dict]]:
    meta_by_symbol = {item["symbol"]: item for item in symbols_meta}
    trading_dates = sorted(bars.keys())
    enriched: dict[date, dict[str, dict]] = {}
    prev_close: dict[str, float] = {}

    for trade_date in trading_dates:
        enriched[trade_date] = {}
        for symbol, bar in bars[trade_date].items():
            item = dict(bar)
            symbol_prev = prev_close.get(symbol)
            limit_base = symbol_prev if symbol_prev is not None else bar["open"]
            item["prev_close"] = symbol_prev
            meta = meta_by_symbol.get(symbol, {})
            if "limit_up" not in item or "limit_down" not in item:
                limit_up, limit_down = compute_limit_prices(
                    limit_base,
                    st_flag=meta.get("st_flag", False),
                    board=meta.get("board", "main"),
                )
                item["limit_up"] = limit_up
                item["limit_down"] = limit_down
            enriched[trade_date][symbol] = item
            prev_close[symbol] = bar["close"]

    return enriched
