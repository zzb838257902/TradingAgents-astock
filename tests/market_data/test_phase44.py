"""Phase 4.4 industry/index universe resolver and board membership PIT."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tradingagents.market_data.contracts import MembershipMode, PITLevel
from tradingagents.market_data.fixture_store import load_fixture_into_repository
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.screener.universe_resolver import (
    UniverseRequest,
    UniverseResolver,
    UniverseType,
    validate_universe_request,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _signal_time(value: date) -> datetime:
    return datetime.combine(value, datetime.min.time().replace(hour=15, minute=30), tzinfo=SHANGHAI)


def _seed_memberships(repo: MarketDataRepository) -> None:
    from tradingagents.market_data.contracts import SecurityRecord

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
    ])
    repo.upsert_board_definitions([{
        "board_type": "industry",
        "board_code": "801080.SI",
        "name": "电子",
        "pit_level": PITLevel.PIT_REQUIRED.value,
        "source": "fixture",
        "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
    }])
    repo.upsert_board_definitions([{
        "board_type": "index",
        "board_code": "000300.SH",
        "name": "沪深300",
        "pit_level": PITLevel.PIT_REQUIRED.value,
        "source": "fixture",
        "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
    }])
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
            "effective_from": date(2026, 1, 1),
            "effective_to": None,
            "snapshot_date": None,
            "available_at": datetime(2026, 1, 1, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
        {
            "board_type": "index",
            "board_code": "000300.SH",
            "symbol": "600001",
            "membership_mode": MembershipMode.EFFECTIVE_INTERVAL.value,
            "effective_from": date(2025, 6, 1),
            "effective_to": date(2025, 12, 1),
            "snapshot_date": None,
            "available_at": datetime(2025, 6, 1, 9, 0, tzinfo=SHANGHAI),
            "source": "fixture",
        },
    ])
    for snapshot_date in (
        date(2025, 5, 1),
        date(2025, 12, 18),
        date(2026, 6, 1),
    ):
        repo.seed_security_snapshot_for_date(snapshot_date, _signal_time(snapshot_date))


def test_validate_industry_requires_code():
    request = UniverseRequest(
        universe_type=UniverseType.INDUSTRY,
        as_of=_signal_time(date(2025, 12, 18)),
    )
    errors = validate_universe_request(request)
    assert "universe_code" in errors[0]


def test_industry_membership_respects_effective_interval(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_memberships(repo)
    resolver = UniverseResolver(repo)
    early = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.INDUSTRY,
        universe_code="801080.SI",
        as_of=_signal_time(date(2025, 12, 18)),
    ))
    assert early.is_ok
    assert early.symbols == ["600001"]
    late = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.INDUSTRY,
        universe_code="801080.SI",
        as_of=_signal_time(date(2026, 6, 1)),
    ))
    assert set(late.symbols) == {"600001", "600002"}


def test_index_membership_does_not_use_future_members(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_memberships(repo)
    resolver = UniverseResolver(repo)
    result = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.INDEX,
        universe_code="000300.SH",
        as_of=_signal_time(date(2025, 5, 1)),
    ))
    assert result.is_ok
    assert result.symbols == []


def test_custom_universe_intersects_effective_securities(tmp_path):
    from tradingagents.market_data.contracts import SecurityRecord

    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_security_records([SecurityRecord(
        symbol="600001",
        name="A",
        board="main",
        valid_from=date(2020, 1, 1),
        list_date=date(2020, 1, 1),
        status="listed",
        st_flag=False,
        available_at=datetime(2020, 1, 1, 9, 0, tzinfo=SHANGHAI),
        source="fixture",
    )])
    repo.seed_security_snapshot_for_date(date(2025, 12, 18), _signal_time(date(2025, 12, 18)))
    resolver = UniverseResolver(repo)
    result = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.CUSTOM,
        symbols=("600001", "600099"),
        as_of=_signal_time(date(2025, 12, 18)),
    ))
    assert result.is_ok
    assert result.symbols == ["600001"]


def test_fixture_store_loads_board_memberships(tmp_path):
    import json
    from pathlib import Path

    fixture = json.loads(Path("tests/fixtures/market_data/provider_mini.json").read_text())
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    load_fixture_into_repository(repo, fixture)
    memberships = repo.get_board_memberships(
        "industry",
        "801080.SI",
        date(2026, 1, 3),
        datetime(2026, 1, 3, 15, 30, tzinfo=SHANGHAI),
    )
    assert len(memberships) == 1
    assert memberships[0].symbol == "600001"
