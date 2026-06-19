# Auto Stock Screening MVP Verification Report

> Date: 2026-06-19  
> Commit tested: `5e81623` (pre-ruff fix; engine lint fix pending commit)  
> Environment: WSL2, Python 3.14.4, Linux x86_64

## Acceptance commands

### 1. Dependency installation

```bash
python3 -m pip install -e ".[screener,dev]"
```

**Exit code:** 1  
**Observed:** `pyarrow` wheel build failed (`cmake` not available on host). MVP runtime does not import `pyarrow`; `duckdb`, `PyYAML`, `pandas`, and core project deps install successfully via:

```bash
python3 -m pip install --target=.pip_packages -e .
python3 -m pip install --target=.pip_packages duckdb PyYAML pandas pytest pytest-cov typer
```

**Risk:** `[screener]` extra is not fully installable on Python 3.14 without build toolchain until `pyarrow` publishes a 3.14 wheel or becomes optional.

### 2. Test suite

```bash
PYTHONPATH=".pip_packages:." python3 -m pytest tests --ignore=tests/test_google_api_key.py -v \
  --cov=tradingagents.market_data --cov=tradingagents.screener --cov=tradingagents.backtest
```

**Exit code:** 0  
**Results:** 135 passed, 44 subtests passed, 0 failures  
**Coverage (new modules):** 92% total (509 statements, 42 missed)

| Module | Cover |
|--------|-------|
| market_data/contracts.py | 100% |
| market_data/repository.py | 91% |
| market_data/providers/existing_astock.py | 91% |
| screener/config.py | 100% |
| screener/universe.py | 100% |
| screener/strategy.py | 100% |
| backtest/metrics.py | 100% |
| backtest/engine.py | 92% |
| backtest/execution.py | 91% |

**Note:** `tests/test_google_api_key.py` skipped — requires `[google]` extra; `langchain_google_genai` / `zstandard` failed to build on Python 3.14 (pre-existing optional path).

### 3. Ruff

```bash
ruff check tradingagents/market_data tradingagents/screener tradingagents/backtest tests/screener
```

**Exit code:** 0 (after fixing `E741` ambiguous name in `backtest/engine.py`)  
**Output:** `All checks passed!`

### 4. data-health

```bash
PYTHONPATH=".pip_packages:." python3 -m tradingagents.screener.cli data-health
```

**Exit code:** 0  
**Observed labels:**

| Dataset | PIT level |
|---------|-----------|
| daily_bars | pit_required |
| security_master | pit_required |
| financials | pit_required |
| adjustment_factors | best_effort |
| concept_members | current_only |
| fund_flow | best_effort |

### 5. backtest-fixture

```bash
PYTHONPATH=".pip_packages:." python3 -m tradingagents.screener.cli backtest-fixture \
  --fixture tests/fixtures/screener/mvp_market.json \
  --home-dir /tmp/tradingagents-mvp-final
```

**Exit code:** 0  
**fixture_sha256:** `1fecccc881ec39ce7ed8dc68b774c0e812e8a0ebf87a6983c2b45104544f53cd`  
**config_hash:** `daef7785e34994c8deeadf76ead9e8b9142b4b7ee7c81e7b1f903f0f79f53c8f`  
**Sample metrics:** total_return ≈ 0.0042, sharpe ≈ 2.12, positions = 3, orders = 3  
**Reproducibility:** two consecutive runs produced byte-identical JSON (`diff` empty).

### 6. Git cleanliness (before this report commit)

Untracked only: `.pip_packages/` (local dev install target, not committed).

## Plan-level gates

| Gate | Status |
|------|--------|
| Data capability matrix declares all MVP datasets | PASS |
| Historical query includes later-delisted symbol | PASS (`test_repository`) |
| CURRENT_ONLY rejected in historical mode | PASS (`test_contracts`) |
| Signals execute next trading day | PASS (`test_backtest_engine`) |
| T+1, limits, lots, capacity | PASS (`test_execution`, `test_portfolio`) |
| Fixture runs reproducible | PASS |
| Existing single-stock tests | PASS (135/135 excl. optional google) |

## Known capability downgrades (by design)

- `adjustment_factors`: **BEST_EFFORT** until provider audit proves history
- `concept_members`, `fund_flow`: **CURRENT_ONLY** / **BEST_EFFORT** — excluded from formal historical backtest
- `ExistingAStockProvider`: daily bars only; no financials/security list in MVP adapter

## Unresolved risks

1. **pyarrow** in `[screener]` extra blocks clean `pip install -e ".[screener,dev]"` on Python 3.14 without cmake.
2. **Google optional tests** require separate environment with `[google]` wheels.
3. **Commit ordering:** Tasks 7–9 commits landed after Task 12 due to batch staging; all code present on `main`.
4. **Production data ingestion:** MVP uses fixture + adapter contract tests; full mootdx/sina bulk ingest not in scope.

## Commits (MVP Tasks 1–12)

```
7c0d755 feat: add screener configuration
7d3cc3e feat: define point-in-time data contracts
a07be36 docs: audit screener data capabilities
e3c1d89 feat: add point-in-time market repository
4e897d6 feat: add historical universe filters
12e0725 feat: model deterministic A-share execution
fbd6c38 feat: add reproducible screening backtest
b5f0c19 feat: expose automatic screening MVP
a8e8ae4 feat: add MVP screening factors
6b23dcc feat: add fixed ensemble ranking
5e81623 feat: construct constrained stock suggestions
```
