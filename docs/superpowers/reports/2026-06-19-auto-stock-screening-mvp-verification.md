# Auto Stock Screening MVP Verification Report

> Date: 2026-06-19 (post-defect-fix)  
> Commit tested: `2aa27015`  
> Environment: WSL2, Python 3.14.4, Linux x86_64

## Defect fixes verified

| ID | Issue | Fix | Test evidence |
|----|-------|-----|---------------|
| P0-1 | Signal lookahead in fixture pipeline | `pipeline.py` uses single `signal_date`; factors only ≤ signal_date | `test_pipeline_lookahead.py` |
| P0-2 | Sizing used same-day close | `BacktestEngine` sizes with prior-day close | `test_backtest_sizing.py` |
| P0-3 | PIT gate bypassed | Repository + `require_pit_required` + fixture load | `test_pit_integration.py` |
| P0-4 | Per-symbol momentum rank | Cross-section `rank_score` on raw momentum | `test_factors.py` |
| P1-1 | Equal-weight override | Portfolio market-value weights in targets | `test_portfolio_backtest.py` |
| P1-2 | Guessed limit prices | `limits.py` rule-based limits; strict `bar_from_dict` | `test_limits.py` |
| P1-3 | Calendar days / delisting | Trading-day filter; delist before missing-bar | `test_universe.py`, `test_backtest_engine.py` |
| P1-4 | Dev environment | `pyarrow` optional; google test skip; `.pip_packages/` ignored | see below |

## Acceptance commands (observed)

### 1. Dependency installation

```bash
python3 -m pip install -e ".[screener,dev]"
```

**Note:** On Python 3.14 without cmake, use `pip install -e ".[screener,dev]"` after removing mandatory `pyarrow` from `[screener]` (moved to `[screener-arrow]`). MVP runtime does not import pyarrow.

### 2. Screener tests

```bash
PYTHONPATH=".pip_packages:." python3 -m pytest tests/screener -v \
  --cov=tradingagents.market_data --cov=tradingagents.screener --cov=tradingagents.backtest
```

**Exit code:** 0  
**Results:** 41 passed  
**Coverage:** 92% (667 statements, 53 missed)

### 3. Full test suite

```bash
PYTHONPATH=".pip_packages:." python3 -m pytest tests -v
```

**Exit code:** 0  
**Results:** 148 passed, 1 skipped (`test_google_api_key` — `pytest.importorskip("langchain_google_genai")`)

### 4. Ruff

```bash
ruff check tradingagents/market_data tradingagents/screener tradingagents/backtest tests/screener
```

**Exit code:** 0 — `All checks passed!`

### 5. data-health

**Exit code:** 0 — labels `concept_members`/`fund_flow` as `current_only`/`best_effort`; core datasets `pit_required`.

### 6. backtest-fixture reproducibility

```bash
tradingagents-screen backtest-fixture --fixture tests/fixtures/screener/mvp_market.json --home-dir /tmp/tradingagents-mvp-final
```

**fixture_sha256:** `8ff4c1b6d8251ea4b2f247ede2c4c4a01342b489c06761ab0a10e978af0813dc`  
**target_weights (non-equal):** 600001≈8.8%, 600002≈24.6%, 600003≈66.6%  
**Two consecutive runs:** `diff` empty (byte-identical JSON)

## Anti-lookahead gates

| Gate | Status |
|------|--------|
| Post-signal close change does not alter ranking/orders | PASS |
| Execution-day close change does not alter fill shares | PASS |
| Future financial `available_at` ignored | PASS |
| `BEST_EFFORT` dataset rejected in historical backtest | PASS |
| `available_at` enforced on repository queries | PASS |

## Remaining risks

1. Production bulk ingest still out of MVP scope.
2. `[google]` extra still requires compatible wheels on Python 3.14.
3. First-day limit prices use `open` as limit base when no prior close exists (documented in `limits.py`).
