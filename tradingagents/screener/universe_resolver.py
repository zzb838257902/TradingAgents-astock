"""Resolve screening universes (all/industry/index/custom) with PIT membership."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagents.market_data.contracts import Membership, PITLevel
from tradingagents.market_data.market_hours import ensure_aware_shanghai
from tradingagents.market_data.repository import MarketDataRepository


def normalize_board_query(text: str) -> str:
    return text.strip().lower()


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
_UNIVERSE_BY_BOARD_TYPE = {value: key for key, value in _BOARD_TYPE_BY_UNIVERSE.items()}


class GroupFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: tuple[str, ...] = ()
    mode: Literal["any", "all"] = "any"


class CompositeUniverseQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    industries: GroupFilter | None = None
    concepts: GroupFilter | None = None
    indices: GroupFilter | None = None
    custom_symbols: tuple[str, ...] = ()
    groups_combine: Literal["and", "or"] = "and"
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def _normalize_as_of(cls, value: datetime) -> datetime:
        return ensure_aware_shanghai(value)


class BoardMatchKind(StrEnum):
    CODE = "code"
    NAME = "name"
    ALIAS = "alias"
    FUZZY = "fuzzy"


class BoardMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    board_type: str
    board_code: str
    name: str
    match_kind: BoardMatchKind


class BoardResolveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: BoardMatch | None = None
    candidates: list[BoardMatch] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        return self.match is not None and not self.errors


class BoardNameResolver:
    def __init__(self, repository: MarketDataRepository):
        self.repository = repository

    def resolve(self, board_type: str, query: str) -> BoardResolveResult:
        text = query.strip()
        if not text:
            return BoardResolveResult(errors=["empty board query"])

        definition = self.repository.get_board_definition(board_type, text)
        if definition is not None:
            return BoardResolveResult(match=BoardMatch(
                board_type=definition["board_type"],
                board_code=definition["board_code"],
                name=definition["name"],
                match_kind=BoardMatchKind.CODE,
            ))

        exact_names = self.repository.find_boards_by_exact_name(board_type, text)
        if len(exact_names) == 1:
            row = exact_names[0]
            return BoardResolveResult(match=BoardMatch(
                board_type=row["board_type"],
                board_code=row["board_code"],
                name=row["name"],
                match_kind=BoardMatchKind.NAME,
            ))
        if len(exact_names) > 1:
            return BoardResolveResult(candidates=[
                BoardMatch(
                    board_type=row["board_type"],
                    board_code=row["board_code"],
                    name=row["name"],
                    match_kind=BoardMatchKind.NAME,
                )
                for row in exact_names
            ])

        aliases = [
            row for row in self.repository.lookup_board_aliases(normalize_board_query(text))
            if row["board_type"] == board_type
        ]
        alias_codes = sorted({row["board_code"] for row in aliases})
        if len(alias_codes) == 1:
            definition = self.repository.get_board_definition(board_type, alias_codes[0])
            if definition is not None:
                return BoardResolveResult(match=BoardMatch(
                    board_type=definition["board_type"],
                    board_code=definition["board_code"],
                    name=definition["name"],
                    match_kind=BoardMatchKind.ALIAS,
                ))
        if len(alias_codes) > 1:
            candidates: list[BoardMatch] = []
            for board_code in alias_codes:
                definition = self.repository.get_board_definition(board_type, board_code)
                if definition is None:
                    continue
                candidates.append(BoardMatch(
                    board_type=definition["board_type"],
                    board_code=definition["board_code"],
                    name=definition["name"],
                    match_kind=BoardMatchKind.ALIAS,
                ))
            return BoardResolveResult(candidates=candidates)

        fuzzy_rows = self.repository.search_board_candidates(board_type, text)
        return BoardResolveResult(candidates=[
            BoardMatch(
                board_type=row["board_type"],
                board_code=row["board_code"],
                name=row["name"],
                match_kind=BoardMatchKind.FUZZY,
            )
            for row in fuzzy_rows
        ])


class UniverseResolver:
    def __init__(self, repository: MarketDataRepository):
        self.repository = repository
        self.board_names = BoardNameResolver(repository)

    def resolve_composite(self, query: CompositeUniverseQuery) -> UniverseResolveResult:
        as_of_date = query.as_of.date()
        available_before = query.as_of
        snapshot_error = self.repository.screening_security_snapshot_error(as_of_date)
        if snapshot_error:
            return UniverseResolveResult(
                errors=[snapshot_error],
                universe_type=UniverseType.ALL,
            )

        effective_symbols = {
            record.symbol
            for record in self.repository.get_effective_securities_for_screening(
                as_of_date, available_before
            )
        }

        symbol_groups: list[set[str]] = []
        if query.custom_symbols:
            requested = {symbol.strip() for symbol in query.custom_symbols if symbol.strip()}
            symbol_groups.append(requested)

        for board_type, group_filter in (
            ("industry", query.industries),
            ("concept", query.concepts),
            ("index", query.indices),
        ):
            if group_filter is None or not group_filter.values:
                continue
            per_value_sets: list[set[str]] = []
            for value in group_filter.values:
                board_result = self.board_names.resolve(board_type, value)
                if board_result.errors:
                    return UniverseResolveResult(
                        errors=board_result.errors,
                        universe_type=UniverseType.ALL,
                    )
                if board_result.candidates and not board_result.is_resolved:
                    labels = ", ".join(
                        f"{item.name}({item.board_code})" for item in board_result.candidates
                    )
                    return UniverseResolveResult(
                        errors=[f"ambiguous {board_type} query {value!r}: {labels}"],
                        universe_type=UniverseType.ALL,
                    )
                if board_result.match is None:
                    return UniverseResolveResult(
                        errors=[f"{board_type} board {value!r} is not defined or not synced"],
                        universe_type=UniverseType.ALL,
                    )
                universe_type = _UNIVERSE_BY_BOARD_TYPE[board_type]
                resolved = self.resolve(UniverseRequest(
                    universe_type=universe_type,
                    universe_code=board_result.match.board_code,
                    as_of=query.as_of,
                ))
                if not resolved.is_ok:
                    return resolved
                per_value_sets.append(set(resolved.symbols))

            if not per_value_sets:
                continue
            if group_filter.mode == "all":
                combined = set.intersection(*per_value_sets)
            else:
                combined: set[str] = set()
                for item in per_value_sets:
                    combined |= item
            symbol_groups.append(combined)

        if not symbol_groups:
            return self.resolve(UniverseRequest(
                universe_type=UniverseType.ALL,
                as_of=query.as_of,
            ))

        if query.groups_combine == "or":
            merged = set()
            for item in symbol_groups:
                merged |= item
        else:
            merged = set.intersection(*symbol_groups)

        symbols = sorted(merged & effective_symbols)
        return UniverseResolveResult(
            symbols=symbols,
            universe_type=UniverseType.ALL,
            raw_member_count=len(merged),
        )

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
        snapshot_error = self.repository.screening_security_snapshot_error(as_of_date)
        if snapshot_error:
            return UniverseResolveResult(
                errors=[snapshot_error],
                universe_type=request.universe_type,
                universe_code=request.universe_code,
            )

        effective_symbols = {
            record.symbol
            for record in self.repository.get_effective_securities_for_screening(
                as_of_date, available_before
            )
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
        if definition is None:
            return UniverseResolveResult(
                errors=[f"{board_type} board {board_code} is not defined or not synced"],
                universe_type=request.universe_type,
                universe_code=board_code,
            )
        pit_level = PITLevel(definition["pit_level"])
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
