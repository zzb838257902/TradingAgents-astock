"""Resolve screening universes (all/industry/index/custom) with PIT membership."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagents.market_data.contracts import Membership, PITLevel
from tradingagents.market_data.market_hours import ensure_aware_shanghai
from tradingagents.market_data.repository import MarketDataRepository


class UniverseType(StrEnum):
    ALL = "all"
    INDUSTRY = "industry"
    CONCEPT = "concept"
    INDEX = "index"
    CUSTOM = "custom"


class UniverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe_type: UniverseType
    universe_code: str | None = None
    symbols: tuple[str, ...] = ()
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def _normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware_shanghai(value)


class UniverseResolveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    universe_type: UniverseType
    universe_code: str | None = None
    pit_level: PITLevel | None = None
    raw_member_count: int = 0

    @property
    def is_ok(self) -> bool:
        return not self.errors

    @property
    def allows_empty_universe(self) -> bool:
        return self.is_ok


def validate_universe_request(request: UniverseRequest) -> list[str]:
    errors: list[str] = []
    if request.universe_type == UniverseType.CUSTOM:
        if not request.symbols:
            errors.append("custom universe requires non-empty symbols")
        if request.universe_code:
            errors.append("custom universe must not include universe_code")
    elif request.universe_type in {
        UniverseType.INDUSTRY,
        UniverseType.CONCEPT,
        UniverseType.INDEX,
    }:
        if not request.universe_code:
            errors.append(f"{request.universe_type.value} universe requires universe_code")
        if request.symbols:
            errors.append("board universe must not include symbols list")
    elif request.universe_type == UniverseType.ALL:
        if request.universe_code:
            errors.append("all universe must not include universe_code")
        if request.symbols:
            errors.append("all universe must not include symbols list")
    return errors


_BOARD_TYPE_BY_UNIVERSE = {
    UniverseType.INDUSTRY: "industry",
    UniverseType.CONCEPT: "concept",
    UniverseType.INDEX: "index",
}


class UniverseResolver:
    def __init__(self, repository: MarketDataRepository):
        self.repository = repository

    def resolve(self, request: UniverseRequest) -> UniverseResolveResult:
        errors = validate_universe_request(request)
        if errors:
            return UniverseResolveResult(
                errors=errors,
                universe_type=request.universe_type,
                universe_code=request.universe_code,
            )

        as_of_date = request.as_of.date()
        available_before = request.as_of
        effective_symbols = {
            record.symbol
            for record in self.repository.get_effective_securities(as_of_date, available_before)
        }

        if request.universe_type == UniverseType.ALL:
            return UniverseResolveResult(
                symbols=sorted(effective_symbols),
                universe_type=request.universe_type,
                raw_member_count=len(effective_symbols),
            )

        if request.universe_type == UniverseType.CUSTOM:
            requested = {symbol.strip() for symbol in request.symbols if symbol.strip()}
            symbols = sorted(requested & effective_symbols)
            return UniverseResolveResult(
                symbols=symbols,
                universe_type=request.universe_type,
                raw_member_count=len(requested),
            )

        board_type = _BOARD_TYPE_BY_UNIVERSE[request.universe_type]
        board_code = request.universe_code or ""
        definition = self.repository.get_board_definition(board_type, board_code)
        pit_level = PITLevel(definition["pit_level"]) if definition else PITLevel.PIT_REQUIRED
        if pit_level == PITLevel.CURRENT_ONLY and as_of_date < date.today():
            return UniverseResolveResult(
                errors=[f"{board_type} board {board_code} is current_only and cannot be used historically"],
                universe_type=request.universe_type,
                universe_code=board_code,
                pit_level=pit_level,
            )

        memberships = self.repository.get_board_memberships(
            board_type,
            board_code,
            as_of_date,
            available_before,
        )
        member_symbols = sorted({membership.symbol for membership in memberships})
        symbols = sorted(set(member_symbols) & effective_symbols)
        return UniverseResolveResult(
            symbols=symbols,
            universe_type=request.universe_type,
            universe_code=board_code,
            pit_level=pit_level,
            raw_member_count=len(member_symbols),
        )

    def membership_rows(self, request: UniverseRequest) -> list[Membership]:
        if request.universe_type not in _BOARD_TYPE_BY_UNIVERSE:
            return []
        board_type = _BOARD_TYPE_BY_UNIVERSE[request.universe_type]
        return self.repository.get_board_memberships(
            board_type,
            request.universe_code or "",
            request.as_of.date(),
            request.as_of,
        )
