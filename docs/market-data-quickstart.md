# Market data & screening quickstart (phase 4)

## Install

```bash
# Default: free public data sources (no token required)
pip install -e ".[screener]"

# Optional paid enhancement
pip install -e ".[screener,screener-tushare]"
export TUSHARE_TOKEN=your_token
export TRADINGAGENTS_MARKET_DATA_PROVIDER=tushare
```

Fixture/offline tests do not require `TUSHARE_TOKEN`.

## Provider selection

| Priority | Setting |
|----------|---------|
| 1 | CLI `--provider free\|tushare\|fixture` |
| 2 | `TRADINGAGENTS_MARKET_DATA_PROVIDER` |
| 3 | YAML `market_data.provider` |
| 4 | Default: **`free`** |

Rules:

- **`free`** (default): must not read or require `TUSHARE_TOKEN`.
- **`tushare`**: explicit only; missing token → error, **no** silent fallback to free.
- **`fixture`**: tests, CI, and explicit `--fixture` runs.

See `docs/data/data-capability-matrix.yaml` for PIT levels per dataset.

## Database layout

| Path | Purpose |
|------|---------|
| `~/.tradingagents/data/market_live.duckdb` | Live synced market data |
| `~/.tradingagents/data/market.duckdb` | Fixture / test database (`init-db`) |
| `~/.tradingagents/data/raw_snapshots/` | Append-only provider snapshots |
| `~/.tradingagents/scheduler/` | Job attempts and saved run reports |

`tradingagents-market-data init` never overwrites the live database schema in tests; it initializes `market_live.duckdb`.

## Initial sync (free default)

```bash
tradingagents-market-data init
tradingagents-market-data probe          # probes free endpoints only when provider=free
tradingagents-market-data sync --dataset security-master --as-of 2026-06-19
tradingagents-market-data sync --dataset trade-calendar --start 2020-01-01 --end 2026-06-19
tradingagents-market-data sync --dataset daily --start 2026-06-19
tradingagents-market-data sync --dataset financials --as-of 2026-06-19T15:30:00+08:00
tradingagents-market-data status
```

No `TUSHARE_TOKEN` required for the commands above when `provider=free`.

### Optional Tushare sync

```bash
export TRADINGAGENTS_MARKET_DATA_PROVIDER=tushare
export TUSHARE_TOKEN=your_token
tradingagents-market-data probe
tradingagents-market-data sync --dataset memberships --board-type industry --board-code 801080.SI
```

## Screening (fixture)

```bash
tradingagents-screen screen \
  --fixture tests/fixtures/screener/mvp_market.json \
  --universe all
```

Universe modes: `all`, `industry`, `concept`, `index`, `custom` (with `--symbols`).

**Free provider note:** `industry`, `concept`, and `index` universes support **live (today)** screening only (`CURRENT_ONLY`). Historical `as_of` → `data_error`.

Report `status` values:

- `ok` — screening completed
- `empty_universe` — legal empty pool after filters
- `data_error` — blocking data/universe error (exit code 1)

## After-close job (local)

```bash
# Free default path (no token)
tradingagents-scheduler after-close --trade-date 2026-06-19
tradingagents-scheduler status --trade-date 2026-06-19
```

Jobs are idempotent by `job_name + trade_date + config_hash`. A successful run is skipped unless `--force` is set. All attempts are retained under `~/.tradingagents/scheduler/runs/`.

Offline/dev with fixture-backed screening (skips live sync):

```bash
tradingagents-scheduler after-close \
  --trade-date 2026-01-02 \
  --fixture tests/fixtures/market_data/provider_mini.json
```

## Acceptance tiers

| Tier | Requirements |
|------|----------------|
| **Free default** | `init` / `sync` / `after-close` without token; live screening; fixture regression |
| **Formal historical backtest** | Reliable security snapshots per `as_of`; PIT-adjusted prices for momentum; financials with announcement dates; board data at `PIT_REQUIRED` if used |

Experimental `price_basis=raw` mode may exist for exploration but is **not** formal acceptance.

## Recovery

| Symptom | Action |
|---------|--------|
| `TUSHARE_TOKEN is not set` with `provider=tushare` | Set token or switch to `provider=free` explicitly |
| `capability probe failed` (free) | Check network; free endpoints may be down — do not auto-switch to Tushare |
| `daily completeness below threshold` | Re-run `sync --dataset daily`; check vendor coverage |
| `current_only ... cannot be used historically` | Use live screening or upgrade dataset via audited paid/historical source |
| `data_error` on historical board universe | Expected on free path; use `all`/`custom` for historical backtest until PIT board data exists |
| Stale published data | Failed sync does not replace last `PUBLISHED` version; fix and re-sync |

## Regression (developers)

```bash
PYTHONPATH=".pip_packages:." python3 -m pytest tests/ -q
PYTHONPATH=".pip_packages:." python3 -m ruff check tradingagents tests

# Free default path acceptance (offline + fixture scheduler)
PYTHONPATH=".pip_packages:." python3 scripts/accept_free_data_path.py

# Optional live network acceptance (mootdx + public HTTP)
PYTHONPATH=".pip_packages:." python3 scripts/accept_free_data_path.py --live --trade-date 2026-01-02
```

Original single-stock CLI/Web/fixture flows remain available:

```bash
tradingagents --help
tradingagents-web
tradingagents-screen backtest-fixture --fixture tests/fixtures/screener/mvp_market.json
```
