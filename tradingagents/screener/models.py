from datetime import date

from pydantic import BaseModel, ConfigDict


class CandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    name: str
    industry: str
    list_date: date
    st_flag: bool
    suspended: bool
    avg_amount_20d: float


class UniverseResult(BaseModel):
    included: list[CandidateInput]
    excluded_reasons: dict[str, list[str]]


class PositionSuggestion(BaseModel):
    symbol: str
    industry: str
    shares: int
    price: float
    market_value: float


class PortfolioSuggestion(BaseModel):
    positions: list[PositionSuggestion]
    cash: float
