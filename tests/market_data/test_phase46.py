"""Phase 4.6 concept board PIT via dc_member dated snapshots."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from tradingagents.market_data.config import MarketDataPaths
from tradingagents.market_data.contracts import MembershipMode, PITLevel, SecurityRecord
from tradingagents.market_data.providers.fixture import FixtureProvider
from tradingagents.market_data.providers.tushare import map_concept_members_frame
from tradingagents.market_data.repository import MarketDataRepository
from tradingagents.market_data.sync import MarketDataSync
from tradingagents.screener.universe_resolver import UniverseRequest, UniverseResolver, UniverseType

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _signal_time(value: date) -> datetime:
    return datetime.combine(value, datetime.min.time().replace(hour=15, minute=30), tzinfo=SHANGHAI)


def _seed_concept_snapshots(repo: MarketDataRepository) -> None:
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
        "board_type": "concept",
        "board_code": "BK1184.DC",
        "name": "测试概念",
        "pit_level": PITLevel.PIT_REQUIRED.value,
        "source": "fixture",
        "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
    }])
    day_one = date(2026, 1, 2)
    day_two = date(2026, 1, 3)
    repo.upsert_board_memberships([
        {
            "board_type": "concept",
            "board_code": "BK1184.DC",
            "symbol": "600001",
            "membership_mode": MembershipMode.DATED_SNAPSHOT.value,
            "effective_from": day_one,
            "effective_to": day_one + timedelta(days=1),
            "snapshot_date": day_one,
            "available_at": _signal_time(day_one),
            "source": "fixture",
        },
        {
            "board_type": "concept",
            "board_code": "BK1184.DC",
            "symbol": "600002",
            "membership_mode": MembershipMode.DATED_SNAPSHOT.value,
            "effective_from": day_two,
            "effective_to": day_two + timedelta(days=1),
            "snapshot_date": day_two,
            "available_at": _signal_time(day_two),
            "source": "fixture",
        },
    ])
    for snapshot_date in (day_one, day_two):
        repo.seed_security_snapshot_for_date(snapshot_date, _signal_time(snapshot_date))


def test_dated_snapshot_membership_is_trade_date_specific(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    _seed_concept_snapshots(repo)
    resolver = UniverseResolver(repo)
    day_one = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.CONCEPT,
        universe_code="BK1184.DC",
        as_of=_signal_time(date(2026, 1, 2)),
    ))
    day_two = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.CONCEPT,
        universe_code="BK1184.DC",
        as_of=_signal_time(date(2026, 1, 3)),
    ))
    assert day_one.symbols == ["600001"]
    assert day_two.symbols == ["600002"]


def test_current_only_concept_rejects_historical_backtest(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
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
    ])
    repo.seed_security_snapshot_for_date(date(2025, 12, 18), _signal_time(date(2025, 12, 18)))
    repo.upsert_board_definitions([{
        "board_type": "concept",
        "board_code": "BK9999.DC",
        "name": "当前快照概念",
        "pit_level": PITLevel.CURRENT_ONLY.value,
        "source": "fixture",
        "available_at": datetime(2025, 1, 1, 9, 0, tzinfo=SHANGHAI),
    }])
    resolver = UniverseResolver(repo)
    result = resolver.resolve(UniverseRequest(
        universe_type=UniverseType.CONCEPT,
        universe_code="BK9999.DC",
        as_of=_signal_time(date(2025, 12, 18)),
    ))
    assert not result.is_ok
    assert "current_only" in result.errors[0]


def test_map_concept_members_frame_uses_dated_snapshot():
    frame = pd.DataFrame([
        {
            "trade_date": "20260102",
            "ts_code": "BK1184.DC",
            "con_code": "600001.SH",
            "name": "测试",
        }
    ])
    rows = map_concept_members_frame(frame, "BK1184.DC", "tushare")
    assert rows[0].membership_mode == MembershipMode.DATED_SNAPSHOT
    assert rows[0].snapshot_date == date(2026, 1, 2)
    assert rows[0].pit_member_on(date(2026, 1, 2))
    assert not rows[0].pit_member_on(date(2026, 1, 3))


def test_sync_concept_memberships_from_fixture_provider(tmp_path):
    import json
    from pathlib import Path

    fixture = json.loads(Path("tests/fixtures/market_data/provider_mini.json").read_text())
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    paths = MarketDataPaths(home_dir=tmp_path)
    provider = FixtureProvider(fixture)
    sync = MarketDataSync(repo, provider, paths)
    sync.probe_capabilities()
    as_of = _signal_time(date(2026, 1, 2))
    result = sync.sync_board_memberships("concept", "BK1184.DC", as_of, board_name="测试概念")
    assert result.status.value == "published"
    memberships = repo.get_board_memberships("concept", "BK1184.DC", date(2026, 1, 2), as_of)
    assert len(memberships) == 1
    assert memberships[0].symbol == "600001"
