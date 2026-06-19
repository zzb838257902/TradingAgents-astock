from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    side: Side
    shares: int

    def __init__(self, symbol: str, side: Side, shares: int, **data) -> None:
        super().__init__(symbol=symbol, side=side, shares=shares, **data)


class Bar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open: float
    high: float
    low: float
    close: float
    volume: float
    limit_up: float
    limit_down: float
    suspended: bool = False


class Fill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    side: Side
    shares: int
    price: float
    commission: float
    stamp_tax: float
