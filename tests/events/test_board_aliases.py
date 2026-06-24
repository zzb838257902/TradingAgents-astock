"""Board alias resolution and composite universe tests (phase 5 Task 4)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import MembershipMode, PITLevel, SecurityRecord
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.universe_resolver import (
    BoardMatchKind,
    BoardNameResolver,
    CompositeUniverseQuery,
    GroupFilter,
    UniverseRequest,
    UniverseResolver,
    UniverseType,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _signal_time(value: date) -> datetime:
    return datetime.combine(value, datetime.min.time().replace(hour=15, minute=30), tzinfo=SHANGHAI)


def _seed_board_universe(repo: MarketDataRepository) -> None:
    repo.upsert_security_records([
        SecurityRecord(
            symbol="600001",
            name="A",
            board="main",
            valid_from=date(2020, 1, 1),
            list_date=date(2020, 1, 1),
            status="listed",
            st_flag=False,
            available_at=datetime(2020, 1, 1, 9, 0, tzinfo=SHANGHAI),
            source="fixture",
        ),
        SecurityRecord(
            symbol="600002",
            name="B",
            board="main",
            valid_from=date(2020, 1, 1),
            list_date=date(2020, 1, 1),
            status="listed",
            st_flag=False,
            available_at=datetime(2020, 1, 1, 9, 0, tzinfo=SHANGHAI),
            source="fixture",
        ),
        SecurityRecord(
            symbol="600003",
            name="C",
            board="main",
            valid_from=date(2020, 1, 1),
            list_date=date(2020, 1, 1),
            status="listed",
            st_flag=False,
            available_at=datetime(2020, 1, 1, 9, 0, tzinfo=SHANGHAI),
            source="fixture",
        ),
    ])
    repo.seed_security_snapshot_for_date(date(2026, 1, 3), _signal_time(date(2026, 1, 3)))
    repo.upsert_board_definitions([
        {
            "board_type": "industry",
            "board_code": "801080.SI",
            "name": "电子",
            "pit_level": PITLevel.PIT_REQUIRED.value,
            "source": "fixture",
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
        },
        {
            "board_type": "concept",
            "board_code": "BK1184.DC",
            "name": "人工智能",
            "pit_level": PITLevel.PIT_REQUIRED.value,
            "source": "fixture",
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
        },
        {
            "board_type": "concept",
            "board_code": "BK2000.DC",
            "name": "机器人",
            "pit_level": PITLevel.PIT_REQUIRED.value,
            "source": "fixture",
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
        },
        {
            "board_type": "index",
            "board_code": "000300.SH",
            "name": "沪深300",
            "pit_level": PITLevel.PIT_REQUIRED.value,
            "source": "fixture",
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
        },
    ])
    repo.upsert_board_aliases([
        {
            "board_type": "concept",
            "board_code": "BK1184.DC",
            "alias": "AI概念",
            "alias_normalized": "ai概念",
            "source": "fixture",
        },
    ])
    repo.upsert_board_memberships([
        {
            "board_type": "industry",
            "board_code": "801080.SI",
            "symbol": "600001",
            "membership_mode": MembershipMode.EFFECTIVE_INTERVAL.value,
            "effective_from": date(2025, 1, 1),
            "effective_to": None,
            "snapshot_date": None,
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
        {
            "board_type": "industry",
            "board_code": "801080.SI",
            "symbol": "600002",
            "membership_mode": MembershipMode.EFFECTIVE_INTERVAL.value,
            "effective_from": date(2025, 1, 1),
            "effective_to": None,
            "snapshot_date": None,
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
        {
            "board_type": "concept",
            "board_code": "BK1184.DC",
            "symbol": "600001",
            "membership_mode": MembershipMode.EFFECTIVE_INTERVAL.value,
            "effective_from": date(2025, 1, 1),
            "effective_to": None,
            "snapshot_date": None,
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
        {
            "board_type": "concept",
            "board_code": "BK2000.DC",
            "symbol": "600002",
            "membership_mode": MembershipMode.EFFECTIVE_INTERVAL.value,
            "effective_from": date(2025, 1, 1),
            "effective_to": None,
            "snapshot_date": None,
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
        {
            "board_type": "index",
            "board_code": "000300.SH",
            "symbol": "600001",
            "membership_mode": MembershipMode.EFFECTIVE_INTERVAL.value,
            "effective_from": date(2025, 1, 1),
            "effective_to": None,
            "snapshot_date": None,
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
        {
            "board_type": "index",
            "board_code": "000300.SH",
            "symbol": "600003",
            "membership_mode": MembershipMode.EFFECTIVE_INTERVAL.value,
            "effective_from": date(2025, 1, 1),
            "effective_to": None,
            "snapshot_date": None,
            "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
    ])


def test_board_aliases_do_not_mutate_board_definitions(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_board_universe(repo)
    definition = repo.get_board_definition("concept", "BK1184.DC")
    assert definition is not None
    assert definition["name"] == "人工智能"
    aliases = repo.lookup_board_aliases("ai概念")
    assert aliases[0]["board_code"] == "BK1184.DC"
    assert repo.get_board_definition("concept", "BK1184.DC")["name"] == "人工智能"


def test_board_name_resolver_exact_code_name_and_alias(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_board_universe(repo)
    resolver = BoardNameResolver(repo)
    by_code = resolver.resolve("concept", "BK1184.DC")
    by_name = resolver.resolve("concept", "人工智能")
    by_alias = resolver.resolve("concept", "AI概念")
    assert by_code.is_resolved and by_code.match.match_kind == BoardMatchKind.CODE
    assert by_name.is_resolved and by_name.match.match_kind == BoardMatchKind.NAME
    assert by_alias.is_resolved and by_alias.match.match_kind == BoardMatchKind.ALIAS


def test_fuzzy_board_query_returns_candidates_without_auto_select(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_board_universe(repo)
    resolver = BoardNameResolver(repo)
    result = resolver.resolve("concept", "人")
    assert not result.is_resolved
    assert len(result.candidates) >= 2
    assert all(item.match_kind == BoardMatchKind.FUZZY for item in result.candidates)


def test_composite_universe_and_with_field_any(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_board_universe(repo)
    resolver = UniverseResolver(repo)
    result = resolver.resolve_composite(CompositeUniverseQuery(
        industries=GroupFilter(values=("电子",), mode="any"),
        concepts=GroupFilter(values=("人工智能",), mode="any"),
        groups_combine="and",
        as_of=_signal_time(date(2026, 1, 3)),
    ))
    assert result.is_ok
    assert result.symbols == ["600001"]


def test_composite_universe_or_combines_groups(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_board_universe(repo)
    resolver = UniverseResolver(repo)
    result = resolver.resolve_composite(CompositeUniverseQuery(
        concepts=GroupFilter(values=("人工智能",), mode="any"),
        indices=GroupFilter(values=("000300.SH",), mode="any"),
        groups_combine="or",
        as_of=_signal_time(date(2026, 1, 3)),
    ))
    assert result.is_ok
    assert set(result.symbols) == {"600001", "600003"}


def test_composite_universe_all_mode_requires_every_board(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_board_universe(repo)
    resolver = UniverseResolver(repo)
    result = resolver.resolve_composite(CompositeUniverseQuery(
        concepts=GroupFilter(values=("人工智能", "机器人"), mode="all"),
        as_of=_signal_time(date(2026, 1, 3)),
    ))
    assert result.is_ok
    assert result.symbols == []


def test_composite_universe_rejects_ambiguous_board_name(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_board_universe(repo)
    resolver = UniverseResolver(repo)
    result = resolver.resolve_composite(CompositeUniverseQuery(
        concepts=GroupFilter(values=("人",), mode="any"),
        as_of=_signal_time(date(2026, 1, 3)),
    ))
    assert not result.is_ok
    assert "ambiguous" in result.errors[0]


def test_five_universe_types_resolve_via_resolver(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_board_universe(repo)
    resolver = UniverseResolver(repo)
    as_of = _signal_time(date(2026, 1, 3))
    all_result = resolver.resolve(UniverseRequest(universe_type=UniverseType.ALL, as_of=as_of))
    industry = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.INDUSTRY,
        universe_code="801080.SI",
        as_of=as_of,
    ))
    concept = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.CONCEPT,
        universe_code="BK1184.DC",
        as_of=as_of,
    ))
    index = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.INDEX,
        universe_code="000300.SH",
        as_of=as_of,
    ))
    custom = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.CUSTOM,
        symbols=("600003", "600099"),
        as_of=as_of,
    ))
    assert len(all_result.symbols) == 3
    assert industry.symbols == ["600001", "600002"]
    assert concept.symbols == ["600001"]
    assert index.symbols == ["600001", "600003"]
    assert custom.symbols == ["600003"]
