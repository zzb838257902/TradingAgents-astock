# Automatic Stock Screening MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible single-machine MVP that creates a historical A-share universe, stores point-in-time daily and financial data, ranks stocks with a momentum and quality strategy, constructs a constrained suggestion portfolio, and backtests it with deterministic A-share execution rules.

**Architecture:** Introduce one canonical `market_data` repository between providers and consumers, with typed `DataResult` and PIT capability metadata. The MVP is a vertical slice: data capability audit → security effective records → DuckDB snapshots → two rule strategies → fixed ensemble → deterministic backtest → CLI. Existing single-stock Agent tools remain available and are not migrated to the new repository in this plan; the compatibility migration and frozen LLM `AnalysisContext` belong to the V2 plan.

**Tech Stack:** Python 3.11, Pydantic v2, DuckDB, pandas, Typer, pytest, pytest-cov, Ruff, existing TradingAgents-Astock data adapters.

---

## Scope boundary

This plan implements design stages 0–3 only. It deliberately excludes concept/event history, intraday monitoring, dynamic market regimes, ML ranking, Web UI, paid provider implementations, and LLM candidate review. Those become separate V1/V2/V3 plans after this MVP passes its tests and produces a benchmark report.

## Locked file structure

```text
config/
└── screener.example.yaml              # documented MVP configuration
docs/data/
└── data-capability-matrix.yaml         # audited dataset capabilities
tradingagents/market_data/
├── __init__.py
├── contracts.py                        # DataResult, PIT level, normalized records
├── providers/
│   ├── __init__.py
│   ├── base.py                         # provider protocol
│   └── existing_astock.py              # adapter over existing free sources
└── repository.py                       # DuckDB schema and PIT queries
tradingagents/screener/
├── __init__.py
├── config.py                            # validated YAML/environment configuration
├── models.py                            # universe, score and portfolio models
├── universe.py                          # historical universe and hard filters
├── factors.py                           # momentum and financial quality factors
├── strategy.py                          # fixed rule scores and ensemble
├── portfolio.py                         # capacity-aware heuristic constructor
└── cli.py                               # init-data, screen and backtest commands
tradingagents/backtest/
├── __init__.py
├── models.py                            # orders, positions and result models
├── execution.py                         # deterministic A-share fills
├── engine.py                            # event loop and accounting
└── metrics.py                           # baseline performance metrics
tests/screener/
├── test_config.py
├── test_contracts.py
├── test_repository.py
├── test_universe.py
├── test_factors.py
├── test_strategy.py
├── test_portfolio.py
├── test_execution.py
├── test_backtest_engine.py
└── test_cli.py
```

### Task 1: Add MVP dependencies and validated configuration

**Files:**
- Modify: `pyproject.toml`
- Create: `config/screener.example.yaml`
- Create: `tradingagents/screener/__init__.py`
- Create: `tradingagents/screener/config.py`
- Test: `tests/screener/test_config.py`

- [ ] **Step 1: Write the failing configuration tests**

```python
# tests/screener/test_config.py
from pathlib import Path

import pytest

from tradingagents.screener.config import ScreenerConfig


def test_loads_mvp_config(tmp_path: Path):
    path = tmp_path / "screener.yaml"
    path.write_text(
        """
home_dir: /tmp/tradingagents-test
universe:
  min_listing_days: 60
  min_avg_amount_20d: 50000000
strategy:
  momentum_weight: 0.5
  quality_weight: 0.5
portfolio:
  portfolio_value: 1000000
  max_positions: 10
  max_stock_weight: 0.10
  max_industry_weight: 0.25
  cash_buffer: 0.10
""",
        encoding="utf-8",
    )
    config = ScreenerConfig.from_yaml(path)
    assert config.universe.min_listing_days == 60
    assert config.strategy.momentum_weight + config.strategy.quality_weight == 1
    assert config.portfolio.portfolio_value == 1_000_000


def test_rejects_unknown_config_key(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text("unknown_key: true\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ScreenerConfig.from_yaml(path)


def test_rejects_strategy_weights_that_do_not_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        ScreenerConfig.model_validate({
            "strategy": {"momentum_weight": 0.8, "quality_weight": 0.5}
        })
```

- [ ] **Step 2: Run the test and verify the import fails**

Run: `python -m pytest tests/screener/test_config.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'tradingagents.screener'`.

- [ ] **Step 3: Add dependencies and package entry point**

Merge these groups into the existing `[project.optional-dependencies]` table in `pyproject.toml`:

```toml
screener = [
    "duckdb>=1.3,<2",
    "pyarrow>=19,<22",
    "PyYAML>=6.0,<7",
]
dev = [
    "pytest>=8.3,<9",
    "pytest-cov>=6,<8",
    "ruff>=0.11,<1",
]
```

Add to `[project.scripts]`:

```toml
tradingagents-screen = "tradingagents.screener.cli:app"
```

- [ ] **Step 4: Implement strict Pydantic configuration**

```python
# tradingagents/screener/config.py
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UniverseConfig(StrictModel):
    min_listing_days: int = Field(default=60, ge=1)
    min_avg_amount_20d: float = Field(default=50_000_000, ge=0)


class StrategyConfig(StrictModel):
    momentum_weight: float = Field(default=0.5, ge=0, le=1)
    quality_weight: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def validate_weights(self) -> Self:
        if abs(self.momentum_weight + self.quality_weight - 1.0) > 1e-9:
            raise ValueError("strategy weights must sum to 1")
        return self


class PortfolioConfig(StrictModel):
    portfolio_value: float = Field(default=1_000_000, gt=0)
    max_positions: int = Field(default=10, ge=1)
    max_stock_weight: float = Field(default=0.10, gt=0, le=1)
    max_industry_weight: float = Field(default=0.25, gt=0, le=1)
    cash_buffer: float = Field(default=0.10, ge=0, lt=1)
    max_participation_rate: float = Field(default=0.05, gt=0, le=1)


class ScreenerConfig(StrictModel):
    home_dir: Path = Path("~/.tradingagents").expanduser()
    universe: UniverseConfig = UniverseConfig()
    strategy: StrategyConfig = StrategyConfig()
    portfolio: PortfolioConfig = PortfolioConfig()

    @classmethod
    def from_yaml(cls, path: Path) -> "ScreenerConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)
```

Create `config/screener.example.yaml` with the same explicit values used by `test_loads_mvp_config`.

- [ ] **Step 5: Install and verify the tests pass**

Run: `python -m pip install -e ".[screener,dev]"`

Expected: installation completes without dependency resolution errors.

Run: `python -m pytest tests/screener/test_config.py -v`

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml config/screener.example.yaml tradingagents/screener tests/screener/test_config.py
git commit -m "feat: add screener configuration"
```

### Task 2: Define data contracts and PIT capability levels

**Files:**
- Create: `tradingagents/market_data/__init__.py`
- Create: `tradingagents/market_data/contracts.py`
- Test: `tests/screener/test_contracts.py`

- [ ] **Step 1: Write failing contract tests**

```python
# tests/screener/test_contracts.py
from datetime import date, datetime, timezone

from tradingagents.market_data.contracts import (
    DataResult,
    DataStatus,
    PITLevel,
    SecurityRecord,
)


def test_error_result_is_not_usable():
    result = DataResult[list[int]](
        data=None,
        status=DataStatus.ERROR,
        source="test",
        as_of=datetime(2026, 6, 19, tzinfo=timezone.utc),
        available_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        pit_level=PITLevel.PIT_REQUIRED,
        errors=["timeout"],
    )
    assert not result.is_usable


def test_current_only_result_rejected_for_historical_mode():
    result = DataResult[list[int]](
        data=[1],
        status=DataStatus.OK,
        source="test",
        as_of=datetime(2026, 6, 19, tzinfo=timezone.utc),
        available_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        pit_level=PITLevel.CURRENT_ONLY,
    )
    assert not result.usable_in_historical_mode


def test_security_record_uses_effective_dates():
    record = SecurityRecord(
        symbol="600001",
        name="示例股份",
        board="main",
        valid_from=date(2020, 1, 1),
        valid_to=date(2024, 5, 1),
        list_date=date(2000, 1, 1),
        delist_date=date(2024, 5, 1),
        status="listed",
        st_flag=False,
        available_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        source="fixture",
    )
    assert record.was_effective_on(date(2023, 1, 3))
    assert not record.was_effective_on(date(2025, 1, 3))
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_contracts.py -v`

Expected: FAIL because `tradingagents.market_data.contracts` does not exist.

- [ ] **Step 3: Implement the contracts**

```python
# tradingagents/market_data/contracts.py
from datetime import date, datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PITLevel(StrEnum):
    PIT_REQUIRED = "pit_required"
    CURRENT_ONLY = "current_only"
    BEST_EFFORT = "best_effort"


class DataStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    STALE = "stale"
    ERROR = "error"


class DataResult(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")
    data: T | None
    status: DataStatus
    source: str
    as_of: datetime
    available_at: datetime
    pit_level: PITLevel
    errors: list[str] = Field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return self.status in {DataStatus.OK, DataStatus.EMPTY} and not self.errors

    @property
    def usable_in_historical_mode(self) -> bool:
        return self.is_usable and self.pit_level == PITLevel.PIT_REQUIRED


class SecurityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    name: str
    board: str
    valid_from: date
    valid_to: date | None = None
    list_date: date
    delist_date: date | None = None
    status: str
    st_flag: bool
    available_at: datetime
    source: str

    def was_effective_on(self, value: date) -> bool:
        return self.valid_from <= value and (
            self.valid_to is None or value < self.valid_to
        )
```

- [ ] **Step 4: Run contract tests**

Run: `python -m pytest tests/screener/test_contracts.py -v`

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/market_data tests/screener/test_contracts.py
git commit -m "feat: define point-in-time data contracts"
```

### Task 3: Audit data capabilities and define the provider protocol

**Files:**
- Create: `docs/data/data-capability-matrix.yaml`
- Create: `tradingagents/market_data/providers/__init__.py`
- Create: `tradingagents/market_data/providers/base.py`
- Test: `tests/screener/test_data_capabilities.py`

- [ ] **Step 1: Write the matrix validation test**

```python
# tests/screener/test_data_capabilities.py
from pathlib import Path

import yaml


def test_every_dataset_declares_pit_capability():
    path = Path("docs/data/data-capability-matrix.yaml")
    matrix = yaml.safe_load(path.read_text(encoding="utf-8"))
    required = {"daily_bars", "adjustment_factors", "security_master", "financials"}
    assert required <= set(matrix["datasets"])
    for name, definition in matrix["datasets"].items():
        assert definition["pit_level"] in {
            "pit_required", "current_only", "best_effort"
        }, name
        assert definition["history_start"], name
        assert definition["source"], name
```

- [ ] **Step 2: Run and verify missing file failure**

Run: `python -m pytest tests/screener/test_data_capabilities.py -v`

Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Create the audited initial matrix**

```yaml
# docs/data/data-capability-matrix.yaml
version: 1
datasets:
  daily_bars:
    source: mootdx+sina
    history_start: "provider-dependent"
    update_frequency: daily
    available_at: next_post_close_run
    pit_level: pit_required
  adjustment_factors:
    source: provider-audit-required
    history_start: "provider-dependent"
    update_frequency: daily
    available_at: next_post_close_run
    pit_level: best_effort
  security_master:
    source: mootdx+exchange-metadata
    history_start: "provider-dependent"
    update_frequency: daily
    available_at: source_publication_time
    pit_level: pit_required
  financials:
    source: sina+exchange-disclosure-time
    history_start: "provider-dependent"
    update_frequency: daily
    available_at: disclosure_time
    pit_level: pit_required
  concept_members:
    source: eastmoney+baidu
    history_start: current_snapshot_only
    update_frequency: daily
    available_at: ingestion_time
    pit_level: current_only
  fund_flow:
    source: eastmoney
    history_start: limited_recent_history
    update_frequency: intraday
    available_at: ingestion_time
    pit_level: best_effort
```

The value `provider-audit-required` is a deliberate capability sentinel: Task 6 must prevent adjustment factors from being promoted to `pit_required` until an adapter contract test proves historical coverage.

- [ ] **Step 4: Define the provider protocol**

```python
# tradingagents/market_data/providers/base.py
from datetime import date
from typing import Protocol, Sequence

from tradingagents.market_data.contracts import DataResult, SecurityRecord


class MarketDataProvider(Protocol):
    name: str

    def list_securities(self, as_of: date) -> DataResult[list[SecurityRecord]]:
        ...

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> DataResult[list[dict]]:
        ...

    def get_financials(
        self, symbols: Sequence[str], available_before: date
    ) -> DataResult[list[dict]]:
        ...
```

- [ ] **Step 5: Run tests and commit**

Run: `python -m pytest tests/screener/test_data_capabilities.py -v`

Expected: 1 passed.

```bash
git add docs/data tradingagents/market_data/providers tests/screener/test_data_capabilities.py
git commit -m "docs: audit screener data capabilities"
```

### Task 4: Implement the DuckDB repository and migrations

**Files:**
- Create: `tradingagents/market_data/repository.py`
- Test: `tests/screener/test_repository.py`

- [ ] **Step 1: Write failing repository tests**

```python
# tests/screener/test_repository.py
from datetime import date, datetime, timezone

from tradingagents.market_data.contracts import SecurityRecord
from tradingagents.market_data.repository import MarketDataRepository


def security(symbol: str, valid_from: date, valid_to: date | None = None):
    return SecurityRecord(
        symbol=symbol,
        name=symbol,
        board="main",
        valid_from=valid_from,
        valid_to=valid_to,
        list_date=valid_from,
        delist_date=valid_to,
        status="listed",
        st_flag=False,
        available_at=datetime.combine(valid_from, datetime.min.time(), timezone.utc),
        source="fixture",
    )


def test_historical_security_query_includes_later_delisted_stock(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_security_records([
        security("600001", date(2010, 1, 1), date(2024, 1, 1)),
        security("600002", date(2010, 1, 1)),
    ])
    assert repo.list_effective_symbols(date(2023, 1, 3)) == ["600001", "600002"]
    assert repo.list_effective_symbols(date(2025, 1, 3)) == ["600002"]


def test_daily_bar_query_cannot_return_future_rows(tmp_path):
    repo = MarketDataRepository(tmp_path / "market.duckdb")
    repo.upsert_daily_bars([
        {"symbol": "600002", "trade_date": date(2025, 1, 2), "close": 10.0,
         "open": 9.8, "high": 10.2, "low": 9.7, "volume": 1000,
         "amount": 10000.0, "available_at": datetime(2025, 1, 2, 7, 0, tzinfo=timezone.utc),
         "source": "fixture"},
        {"symbol": "600002", "trade_date": date(2025, 1, 3), "close": 11.0,
         "open": 10.0, "high": 11.2, "low": 9.9, "volume": 1200,
         "amount": 12000.0, "available_at": datetime(2025, 1, 3, 7, 0, tzinfo=timezone.utc),
         "source": "fixture"},
    ])
    rows = repo.get_daily_bars(["600002"], end=date(2025, 1, 2))
    assert [row["trade_date"] for row in rows] == [date(2025, 1, 2)]
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_repository.py -v`

Expected: FAIL because `MarketDataRepository` is undefined.

- [ ] **Step 3: Implement schema migration and PIT queries**

Implement `MarketDataRepository` with:

```python
# tradingagents/market_data/repository.py
from datetime import date
from pathlib import Path
from typing import Iterable

import duckdb

from tradingagents.market_data.contracts import SecurityRecord


class MarketDataRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        self._migrate()

    def _migrate(self) -> None:
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS securities (
                symbol VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                board VARCHAR NOT NULL,
                valid_from DATE NOT NULL,
                valid_to DATE,
                list_date DATE NOT NULL,
                delist_date DATE,
                status VARCHAR NOT NULL,
                st_flag BOOLEAN NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(symbol, valid_from)
            );
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol VARCHAR NOT NULL,
                trade_date DATE NOT NULL,
                open DOUBLE NOT NULL,
                high DOUBLE NOT NULL,
                low DOUBLE NOT NULL,
                close DOUBLE NOT NULL,
                volume DOUBLE NOT NULL,
                amount DOUBLE NOT NULL,
                available_at TIMESTAMPTZ NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY(symbol, trade_date, source)
            );
        """)

    def upsert_security_records(self, records: Iterable[SecurityRecord]) -> None:
        rows = [tuple(record.model_dump().values()) for record in records]
        self.connection.executemany(
            "INSERT OR REPLACE INTO securities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def list_effective_symbols(self, as_of: date) -> list[str]:
        rows = self.connection.execute(
            """SELECT symbol FROM securities
               WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)
               ORDER BY symbol""",
            [as_of, as_of],
        ).fetchall()
        return [row[0] for row in rows]
```

Add `upsert_daily_bars()` and `get_daily_bars()` using explicit column lists and `trade_date <= end`; do not use `SELECT *` in the returned mapping.

- [ ] **Step 4: Run repository tests**

Run: `python -m pytest tests/screener/test_repository.py -v`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/market_data/repository.py tests/screener/test_repository.py
git commit -m "feat: add point-in-time market repository"
```

### Task 5: Build historical universes and hard filters

**Files:**
- Create: `tradingagents/screener/models.py`
- Create: `tradingagents/screener/universe.py`
- Test: `tests/screener/test_universe.py`

- [ ] **Step 1: Write failing universe tests**

```python
# tests/screener/test_universe.py
from datetime import date

from tradingagents.screener.models import CandidateInput
from tradingagents.screener.universe import filter_universe


def candidate(symbol: str, **changes):
    values = {
        "symbol": symbol,
        "name": symbol,
        "industry": "电子",
        "list_date": date(2020, 1, 1),
        "st_flag": False,
        "suspended": False,
        "avg_amount_20d": 100_000_000,
    }
    values.update(changes)
    return CandidateInput(**values)


def test_filters_st_new_suspended_and_illiquid_stocks():
    result = filter_universe(
        [
            candidate("A"),
            candidate("B", st_flag=True),
            candidate("C", list_date=date(2025, 12, 20)),
            candidate("D", suspended=True),
            candidate("E", avg_amount_20d=10_000),
        ],
        as_of=date(2026, 1, 5),
        min_listing_days=60,
        min_avg_amount_20d=50_000_000,
    )
    assert [item.symbol for item in result.included] == ["A"]
    assert result.excluded_reasons == {
        "B": ["st"], "C": ["new_listing"], "D": ["suspended"],
        "E": ["illiquid"]
    }
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_universe.py -v`

Expected: FAIL because universe models do not exist.

- [ ] **Step 3: Implement typed universe filtering**

```python
# tradingagents/screener/models.py
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
```

```python
# tradingagents/screener/universe.py
from datetime import date

from tradingagents.screener.models import CandidateInput, UniverseResult


def filter_universe(
    candidates: list[CandidateInput], as_of: date,
    min_listing_days: int, min_avg_amount_20d: float,
) -> UniverseResult:
    included = []
    excluded: dict[str, list[str]] = {}
    for item in candidates:
        reasons = []
        if item.st_flag:
            reasons.append("st")
        if (as_of - item.list_date).days < min_listing_days:
            reasons.append("new_listing")
        if item.suspended:
            reasons.append("suspended")
        if item.avg_amount_20d < min_avg_amount_20d:
            reasons.append("illiquid")
        if reasons:
            excluded[item.symbol] = reasons
        else:
            included.append(item)
    return UniverseResult(included=included, excluded_reasons=excluded)
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/screener/test_universe.py -v`

Expected: 1 passed.

```bash
git add tradingagents/screener/models.py tradingagents/screener/universe.py tests/screener/test_universe.py
git commit -m "feat: add historical universe filters"
```

### Task 6: Adapt existing free data without promoting unverified PIT data

**Files:**
- Create: `tradingagents/market_data/providers/existing_astock.py`
- Modify: `docs/data/data-capability-matrix.yaml`
- Test: `tests/screener/test_existing_astock_provider.py`

- [ ] **Step 1: Write adapter contract tests with monkeypatched existing functions**

```python
# tests/screener/test_existing_astock_provider.py
from datetime import date

from tradingagents.market_data.contracts import DataStatus, PITLevel
from tradingagents.market_data.providers.existing_astock import ExistingAStockProvider


def test_daily_bars_are_normalized_and_pit(monkeypatch):
    csv = "Date,Open,High,Low,Close,Volume\n2026-06-18,10,11,9,10.5,1000\n"
    monkeypatch.setattr(
        "tradingagents.market_data.providers.existing_astock.get_stock_data",
        lambda symbol, start, end: "# source\n" + csv,
    )
    result = ExistingAStockProvider().get_daily_bars(
        ["600000"], date(2026, 6, 18), date(2026, 6, 18)
    )
    assert result.status == DataStatus.OK
    assert result.pit_level == PITLevel.PIT_REQUIRED
    assert result.data[0]["symbol"] == "600000"
    assert result.data[0]["close"] == 10.5


def test_error_text_becomes_error_status(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.market_data.providers.existing_astock.get_stock_data",
        lambda *args: "K线数据获取失败：source unavailable",
    )
    result = ExistingAStockProvider().get_daily_bars(
        ["600000"], date(2026, 6, 18), date(2026, 6, 18)
    )
    assert result.status == DataStatus.ERROR
    assert result.data is None
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_existing_astock_provider.py -v`

Expected: FAIL because `ExistingAStockProvider` is undefined.

- [ ] **Step 3: Implement the narrow adapter**

Implement only daily-bar normalization in this task. Parse the CSV body after comment lines, set `available_at` to the post-close timestamp for each trade date, and convert recognized failure strings to `DataStatus.ERROR`. Return `PITLevel.PIT_REQUIRED` only because the existing function filters by requested dates and the adapter test freezes its response.

Do not adapt current fundamentals, northbound flow, concept members, current industry rank, or realtime fund flow in this task. Their matrix status remains `current_only` or `best_effort`.

- [ ] **Step 4: Add malformed-row coverage**

```python
def test_malformed_row_rejects_complete_symbol_batch(monkeypatch):
    csv = "Date,Open,High,Low,Close,Volume\n2026-06-18,10,11,9,bad,1000\n"
    monkeypatch.setattr(
        "tradingagents.market_data.providers.existing_astock.get_stock_data",
        lambda *args: "# source\n" + csv,
    )
    result = ExistingAStockProvider().get_daily_bars(
        ["600000"], date(2026, 6, 18), date(2026, 6, 18)
    )
    assert result.status == DataStatus.ERROR
    assert result.data is None
    assert "600000 row 2" in result.errors[0]
```

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/screener/test_existing_astock_provider.py -v`

Expected: 3 passed.

```bash
git add tradingagents/market_data/providers/existing_astock.py docs/data/data-capability-matrix.yaml tests/screener/test_existing_astock_provider.py
git commit -m "feat: adapt verified A-share daily bars"
```

### Task 7: Implement momentum and financial-quality factors

**Files:**
- Create: `tradingagents/screener/factors.py`
- Test: `tests/screener/test_factors.py`

- [ ] **Step 1: Write failing factor tests**

```python
# tests/screener/test_factors.py
import pandas as pd

from tradingagents.screener.factors import compute_momentum, compute_quality, rank_score


def test_momentum_uses_only_rows_up_to_signal_date():
    closes = pd.Series([10.0, 11.0, 12.0, 50.0], index=pd.date_range("2026-01-01", periods=4))
    assert compute_momentum(closes, "2026-01-03", lookback=2) == 0.2


def test_quality_rewards_roe_and_cash_conversion_and_penalizes_leverage():
    good = compute_quality(roe=0.18, operating_cashflow=120, net_profit=100, debt_ratio=0.30)
    weak = compute_quality(roe=0.05, operating_cashflow=20, net_profit=100, debt_ratio=0.80)
    assert good > weak


def test_rank_score_maps_cross_section_to_zero_and_one_hundred():
    scores = rank_score(pd.Series({"A": 1.0, "B": 2.0, "C": 3.0}))
    assert scores.to_dict() == {"A": 0.0, "B": 50.0, "C": 100.0}
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_factors.py -v`

Expected: FAIL because factor functions do not exist.

- [ ] **Step 3: Implement deterministic factor functions**

```python
# tradingagents/screener/factors.py
import pandas as pd


def compute_momentum(closes: pd.Series, signal_date: str, lookback: int) -> float:
    visible = closes.loc[:signal_date]
    if len(visible) < lookback + 1:
        raise ValueError("insufficient price history")
    return round(float(visible.iloc[-1] / visible.iloc[-lookback - 1] - 1), 10)


def compute_quality(
    roe: float, operating_cashflow: float, net_profit: float, debt_ratio: float
) -> float:
    cash_conversion = operating_cashflow / max(abs(net_profit), 1e-9)
    return 0.45 * roe + 0.35 * cash_conversion - 0.20 * debt_ratio


def rank_score(values: pd.Series) -> pd.Series:
    if values.isna().any():
        raise ValueError("factor values must be imputed or excluded before ranking")
    if len(values) == 1:
        return pd.Series(50.0, index=values.index)
    return (values.rank(method="average") - 1) / (len(values) - 1) * 100
```

- [ ] **Step 4: Run and commit**

Run: `python -m pytest tests/screener/test_factors.py -v`

Expected: 3 passed.

```bash
git add tradingagents/screener/factors.py tests/screener/test_factors.py
git commit -m "feat: add MVP screening factors"
```

### Task 8: Score two strategies and preserve absolute and group ranks

**Files:**
- Extend: `tradingagents/screener/models.py`
- Create: `tradingagents/screener/strategy.py`
- Test: `tests/screener/test_strategy.py`

- [ ] **Step 1: Write failing scoring tests**

```python
# tests/screener/test_strategy.py
import pandas as pd

from tradingagents.screener.strategy import score_candidates


def test_fixed_ensemble_keeps_absolute_and_industry_rank():
    frame = pd.DataFrame([
        {"symbol": "A", "industry": "电子", "momentum": 80, "quality": 60},
        {"symbol": "B", "industry": "电子", "momentum": 60, "quality": 80},
        {"symbol": "C", "industry": "银行", "momentum": 20, "quality": 100},
    ])
    result = score_candidates(frame, momentum_weight=0.5, quality_weight=0.5)
    assert result.set_index("symbol").loc["A", "ensemble_score"] == 70
    assert set(result.columns) >= {"absolute_rank", "industry_rank"}


def test_rejects_weights_not_summing_to_one():
    frame = pd.DataFrame([{"symbol": "A", "industry": "电子", "momentum": 80, "quality": 60}])
    try:
        score_candidates(frame, momentum_weight=0.8, quality_weight=0.8)
    except ValueError as exc:
        assert "sum to 1" in str(exc)
    else:
        raise AssertionError("expected invalid weights to fail")
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_strategy.py -v`

Expected: FAIL because `score_candidates` is undefined.

- [ ] **Step 3: Implement fixed ensemble scoring**

```python
# tradingagents/screener/strategy.py
import pandas as pd


def score_candidates(
    frame: pd.DataFrame, momentum_weight: float, quality_weight: float
) -> pd.DataFrame:
    if abs(momentum_weight + quality_weight - 1.0) > 1e-9:
        raise ValueError("strategy weights must sum to 1")
    result = frame.copy()
    result["ensemble_score"] = (
        result["momentum"] * momentum_weight + result["quality"] * quality_weight
    )
    result["absolute_rank"] = result["ensemble_score"].rank(
        method="min", ascending=False
    ).astype(int)
    result["industry_rank"] = result.groupby("industry")["ensemble_score"].rank(
        method="min", ascending=False
    ).astype(int)
    return result.sort_values(["ensemble_score", "symbol"], ascending=[False, True])
```

- [ ] **Step 4: Run and commit**

Run: `python -m pytest tests/screener/test_strategy.py -v`

Expected: 2 passed.

```bash
git add tradingagents/screener/models.py tradingagents/screener/strategy.py tests/screener/test_strategy.py
git commit -m "feat: add fixed ensemble ranking"
```

### Task 9: Construct a capacity-aware suggestion portfolio

**Files:**
- Create: `tradingagents/screener/portfolio.py`
- Test: `tests/screener/test_portfolio.py`

- [ ] **Step 1: Write failing portfolio tests**

```python
# tests/screener/test_portfolio.py
import pandas as pd

from tradingagents.screener.portfolio import construct_portfolio


def test_enforces_cash_stock_industry_and_lot_constraints():
    candidates = pd.DataFrame([
        {"symbol": "A", "industry": "电子", "score": 100, "price": 10, "avg_volume": 1_000_000},
        {"symbol": "B", "industry": "电子", "score": 90, "price": 20, "avg_volume": 1_000_000},
        {"symbol": "C", "industry": "银行", "score": 80, "price": 5, "avg_volume": 1_000_000},
    ])
    result = construct_portfolio(
        candidates,
        portfolio_value=100_000,
        max_positions=3,
        max_stock_weight=0.40,
        max_industry_weight=0.60,
        cash_buffer=0.10,
        max_participation_rate=0.05,
    )
    assert all(position.shares % 100 == 0 for position in result.positions)
    assert sum(position.market_value for position in result.positions) <= 90_000
    assert result.cash >= 10_000
    electronic = sum(p.market_value for p in result.positions if p.industry == "电子")
    assert electronic <= 60_000
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_portfolio.py -v`

Expected: FAIL because `construct_portfolio` is undefined.

- [ ] **Step 3: Implement a deterministic greedy constructor**

Add `PositionSuggestion` and `PortfolioSuggestion` Pydantic models to `models.py`. Implement a stable score-descending greedy allocation that:

1. reserves `cash_buffer` first;
2. caps each target by stock weight, remaining industry budget, remaining cash, and `avg_volume * max_participation_rate * price`;
3. rounds shares down to 100-share lots;
4. skips zero-share positions;
5. recalculates all constraints after rounding;
6. returns unallocated money as cash.

Use `Decimal` for money inside the allocator and convert to float only in the returned Pydantic model.

- [ ] **Step 4: Add a small-account regression test**

```python
def test_small_account_returns_all_cash():
    candidates = pd.DataFrame([
        {"symbol": "A", "industry": "电子", "score": 100,
         "price": 20, "avg_volume": 1_000_000},
    ])
    result = construct_portfolio(
        candidates,
        portfolio_value=1_000,
        max_positions=1,
        max_stock_weight=0.10,
        max_industry_weight=0.25,
        cash_buffer=0.10,
        max_participation_rate=0.05,
    )
    assert result.positions == []
    assert result.cash == 1_000
```

- [ ] **Step 5: Run and commit**

Run: `python -m pytest tests/screener/test_portfolio.py -v`

Expected: 2 passed.

```bash
git add tradingagents/screener/models.py tradingagents/screener/portfolio.py tests/screener/test_portfolio.py
git commit -m "feat: construct constrained stock suggestions"
```

### Task 10: Implement deterministic A-share execution

**Files:**
- Create: `tradingagents/backtest/__init__.py`
- Create: `tradingagents/backtest/models.py`
- Create: `tradingagents/backtest/execution.py`
- Test: `tests/screener/test_execution.py`

- [ ] **Step 1: Write failing execution tests**

```python
# tests/screener/test_execution.py
from tradingagents.backtest.execution import ExecutionModel
from tradingagents.backtest.models import Bar, Order, Side


def test_cannot_buy_one_word_limit_up():
    model = ExecutionModel(commission_rate=0.0003, stamp_tax_rate=0.0005)
    bar = Bar(open=11, high=11, low=11, close=11, volume=100000, limit_up=11, limit_down=9)
    fill = model.fill(Order("600000", Side.BUY, 1000), bar, sellable_shares=0)
    assert fill is None


def test_t_plus_one_blocks_same_day_sale():
    model = ExecutionModel(commission_rate=0.0003, stamp_tax_rate=0.0005)
    bar = Bar(open=10, high=10.5, low=9.8, close=10.2, volume=100000, limit_up=11, limit_down=9)
    fill = model.fill(Order("600000", Side.SELL, 1000), bar, sellable_shares=0)
    assert fill is None


def test_participation_rate_limits_fill_quantity():
    model = ExecutionModel(
        commission_rate=0.0003, stamp_tax_rate=0.0005,
        max_participation_rate=0.05,
    )
    bar = Bar(open=10, high=10.5, low=9.8, close=10.2, volume=10_000, limit_up=11, limit_down=9)
    fill = model.fill(Order("600000", Side.BUY, 1000), bar, sellable_shares=0)
    assert fill.shares == 500
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_execution.py -v`

Expected: FAIL because the backtest package does not exist.

- [ ] **Step 3: Implement models and execution rules**

Define `Side`, `Order`, `Bar`, and `Fill` in `backtest/models.py`. Implement `ExecutionModel.fill()` with this order:

1. reject suspended or zero-volume bars;
2. reject one-word limit-up buys and one-word limit-down sells;
3. cap sells by `sellable_shares`;
4. cap quantity by `floor(volume * max_participation_rate / 100) * 100`;
5. use next-open price plus configurable adverse slippage;
6. charge commission on buys and sells and stamp tax only on sells;
7. return `None` when the final quantity is zero.

- [ ] **Step 4: Run and commit**

Run: `python -m pytest tests/screener/test_execution.py -v`

Expected: 3 passed.

```bash
git add tradingagents/backtest tests/screener/test_execution.py
git commit -m "feat: model deterministic A-share execution"
```

### Task 11: Build the minimal backtest loop and metrics

**Files:**
- Create: `tradingagents/backtest/engine.py`
- Create: `tradingagents/backtest/metrics.py`
- Test: `tests/screener/test_backtest_engine.py`

- [ ] **Step 1: Write a two-day failing integration test**

```python
# tests/screener/test_backtest_engine.py
from datetime import date

from tradingagents.backtest.engine import BacktestEngine
from tradingagents.backtest.execution import ExecutionModel


def test_signal_at_close_executes_next_day_and_respects_t_plus_one():
    bars = {
        date(2026, 1, 2): {"600000": {"open": 10, "high": 10.5, "low": 9.8, "close": 10.2, "volume": 100000}},
        date(2026, 1, 5): {"600000": {"open": 10.3, "high": 10.8, "low": 10.1, "close": 10.7, "volume": 100000}},
        date(2026, 1, 6): {"600000": {"open": 10.8, "high": 11.0, "low": 10.5, "close": 10.9, "volume": 100000}},
    }
    signals = {date(2026, 1, 2): {"600000": 1.0}, date(2026, 1, 5): {}}
    engine = BacktestEngine(
        initial_cash=100_000,
        execution=ExecutionModel(commission_rate=0, stamp_tax_rate=0),
    )
    result = engine.run(bars=bars, target_weights=signals)
    assert result.orders[0].trade_date == date(2026, 1, 5)
    assert result.equity_curve[-1].equity > 100_000
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_backtest_engine.py -v`

Expected: FAIL because `BacktestEngine` is undefined.

- [ ] **Step 3: Implement the event loop**

Implement `BacktestEngine.run()` as a deterministic loop over sorted trading dates. Signals produced on date T become orders at the next available trading date. Track cash, total shares, sellable shares, acquisition date, fees, rejected orders, and end-of-day mark-to-market equity. Shares bought on date T become sellable only on the next trading date.

Fail fast when bars are missing for a held symbol instead of forward-filling a normal close. Record the run configuration and input snapshot ID in `BacktestResult`.

- [ ] **Step 4: Implement baseline metrics**

```python
# tradingagents/backtest/metrics.py
import pandas as pd


def performance_metrics(equity: pd.Series, periods_per_year: int = 252) -> dict[str, float]:
    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    annualized = float((1 + total_return) ** (periods_per_year / max(len(returns), 1)) - 1)
    drawdown = equity / equity.cummax() - 1
    volatility = float(returns.std(ddof=1) * periods_per_year ** 0.5) if len(returns) > 1 else 0.0
    sharpe = float(annualized / volatility) if volatility else 0.0
    return {
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": float(drawdown.min()),
        "annualized_volatility": volatility,
        "sharpe": sharpe,
    }
```

- [ ] **Step 5: Add missing-bar and delisting tests**

```python
import pytest


def test_missing_bar_for_held_symbol_stops_run():
    engine = BacktestEngine(
        initial_cash=100_000,
        execution=ExecutionModel(commission_rate=0, stamp_tax_rate=0),
    )
    bars = {
        date(2026, 1, 2): {"600000": {"open": 10, "high": 10, "low": 10,
                                          "close": 10, "volume": 100000}},
        date(2026, 1, 5): {},
    }
    with pytest.raises(ValueError, match="missing bar for held symbol 600000"):
        engine.run(bars=bars, target_weights={date(2026, 1, 2): {"600000": 1.0}})


def test_delisting_uses_configured_recovery_price():
    engine = BacktestEngine(
        initial_cash=100_000,
        execution=ExecutionModel(commission_rate=0, stamp_tax_rate=0),
        delisting_recovery_rate=0.20,
    )
    result = engine.run(
        bars={
            date(2026, 1, 2): {"600000": {"open": 10, "high": 10.5,
                "low": 9.8, "close": 10.2, "volume": 100000}},
            date(2026, 1, 5): {"600000": {"open": 10.3, "high": 10.8,
                "low": 10.1, "close": 10.7, "volume": 100000}},
            date(2026, 1, 6): {"600000": {"open": 8, "high": 8,
                "low": 8, "close": 8, "volume": 0}},
        },
        target_weights={date(2026, 1, 2): {"600000": 1.0}},
        delistings={date(2026, 1, 6): ["600000"]},
    )
    event = result.delisting_events[0]
    assert event.symbol == "600000"
    assert event.recovery_rate == 0.20
    assert result.positions == {}
```

- [ ] **Step 6: Run and commit**

Run: `python -m pytest tests/screener/test_backtest_engine.py -v`

Expected: 3 passed.

```bash
git add tradingagents/backtest tests/screener/test_backtest_engine.py
git commit -m "feat: add reproducible screening backtest"
```

### Task 12: Add CLI commands and an end-to-end fixture

**Files:**
- Create: `tradingagents/screener/cli.py`
- Test: `tests/screener/test_cli.py`
- Create: `tests/fixtures/screener/mvp_market.json`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI tests**

```python
# tests/screener/test_cli.py
from typer.testing import CliRunner

from tradingagents.screener.cli import app

runner = CliRunner()


def test_data_health_reports_capability_levels():
    result = runner.invoke(app, ["data-health"])
    assert result.exit_code == 0
    assert "daily_bars" in result.stdout
    assert "pit_required" in result.stdout


def test_backtest_fixture_is_reproducible(tmp_path):
    args = [
        "backtest-fixture", "--fixture", "tests/fixtures/screener/mvp_market.json",
        "--home-dir", str(tmp_path),
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout
```

- [ ] **Step 2: Run and verify failure**

Run: `python -m pytest tests/screener/test_cli.py -v`

Expected: FAIL because the CLI module and fixture do not exist.

- [ ] **Step 3: Implement minimal commands**

Create a Typer application with:

```text
data-health       validate and print the data capability matrix
init-db           create/migrate the configured DuckDB file
backtest-fixture  load the committed deterministic fixture and print sorted JSON metrics
```

Every command accepts `--home-dir`. `backtest-fixture` must include a configuration hash and fixture SHA-256 in its JSON output. Use `json.dumps(result, ensure_ascii=False, sort_keys=True)` so repeated runs are byte-identical.

- [ ] **Step 4: Create the deterministic fixture**

The JSON fixture contains three symbols from two industries, 80 trading days of adjusted OHLCV, two financial disclosure snapshots with explicit `available_at`, one later-delisted symbol, and expected benchmark prices. Generate it once with a fixed seed, commit the resulting JSON, and never generate it during the test.

- [ ] **Step 5: Document installation and MVP commands**

Add a README section containing these exact commands:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[screener,dev]"
python -m pytest tests/ -v
tradingagents-screen data-health
tradingagents-screen init-db --home-dir ~/.tradingagents
tradingagents-screen backtest-fixture \
  --fixture tests/fixtures/screener/mvp_market.json \
  --home-dir /tmp/tradingagents-mvp
```

- [ ] **Step 6: Run focused and full verification**

Run: `python -m pytest tests/screener -v`

Expected: all screener tests pass.

Run: `python -m pytest tests -v`

Expected: all existing and new tests pass with zero failures.

Run: `python -m ruff check tradingagents/market_data tradingagents/screener tradingagents/backtest tests/screener`

Expected: `All checks passed!`

Run the `backtest-fixture` command twice and compare its output with `diff`; expected: no differences.

- [ ] **Step 7: Commit**

```bash
git add tradingagents/screener/cli.py tests/fixtures/screener tests/screener/test_cli.py README.md
git commit -m "feat: expose automatic screening MVP"
```

### Task 13: Final MVP acceptance and handoff

**Files:**
- Create: `docs/superpowers/reports/2026-06-19-auto-stock-screening-mvp-verification.md`

- [ ] **Step 1: Run the complete acceptance suite from a clean environment**

Run:

```bash
python -m pip install -e ".[screener,dev]"
python -m pytest tests -v --cov=tradingagents.market_data --cov=tradingagents.screener --cov=tradingagents.backtest
python -m ruff check tradingagents/market_data tradingagents/screener tradingagents/backtest tests/screener
tradingagents-screen data-health
tradingagents-screen backtest-fixture --fixture tests/fixtures/screener/mvp_market.json --home-dir /tmp/tradingagents-mvp-final
git status --short
```

Expected:

- dependency installation succeeds;
- tests report zero failures;
- Ruff reports no errors;
- data-health labels unsupported datasets as `current_only` or `best_effort`;
- fixture backtest prints metrics and snapshot/config hashes;
- `git status --short` is empty before writing the report.

- [ ] **Step 2: Write the verification report with observed evidence**

The report records the exact commands, exit codes, test count, coverage percentages, fixture hashes, runtime, memory peak, known capability downgrades, and the commit SHA tested. Do not copy expected values into the observed-results section; paste actual command output summaries.

- [ ] **Step 3: Commit the verification evidence**

```bash
git add docs/superpowers/reports/2026-06-19-auto-stock-screening-mvp-verification.md
git commit -m "docs: record screening MVP verification"
```

## Plan-level verification gates

Do not start V1 factor expansion until all of these are true:

- the data capability matrix has no undeclared dataset used by the MVP;
- a historical query includes a fixture company that later delists;
- current-only data is rejected in historical mode;
- signals execute no earlier than the next trading day;
- T+1, one-word limits, lot sizes and capacity tests pass;
- two fixture runs produce identical output hashes;
- the existing single-stock tests still pass;
- the verification report records actual evidence.

## Follow-on plans

After MVP acceptance, create separate implementation plans in this order:

1. V1: complete three-strategy factors, industry/index memberships and constrained portfolio reporting;
2. V1.1: current-only concept, announcement, news and fund-flow screening;
3. V1.2: post-close scheduler, recovery and Web/Markdown reports;
4. V2: frozen `AnalysisContext`, lightweight Top 20 review and full Top 5 Agent analysis;
5. V3: ML ranker, validated regime model, pre-open review and intraday observation alerts.
