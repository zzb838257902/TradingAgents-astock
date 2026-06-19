# Market data & screening quickstart (phase 4)

## Install

```bash
pip install -e ".[screener]"
# optional live sync
pip install -e ".[screener,screener-tushare]"
export TUSHARE_TOKEN=your_token
```

Fixture/offline tests do not require `TUSHARE_TOKEN`.

## Database layout

| Path | Purpose |
|------|---------|
| `~/.tradingagents/data/market_live.duckdb` | Live synced market data |
| `~/.tradingagents/data/market.duckdb` | Fixture / test database (`init-db`) |
| `~/.tradingagents/data/raw_snapshots/` | Append-only provider snapshots |
| `~/.tradingagents/scheduler/` | Job attempts and saved run reports |

`tradingagents-market-data init` never overwrites the live database schema in tests; it initializes `market_live.duckdb`.

## Initial sync

```bash
tradingagents-market-data init
tradingagents-market-data probe
tradingagents-market-data sync --dataset security-master --as-of 2026-06-19
tradingagents-market-data sync --dataset trade-calendar --start 2020-01-01 --end 2026-06-19
tradingagents-market-data sync --dataset daily --start 2026-06-19
tradingagents-market-data sync --dataset financials --as-of 2026-06-19T15:30:00+08:00
tradingagents-market-data status
```

## Screening (fixture)

```bash
tradingagents-screen screen \
  --fixture tests/fixtures/screener/mvp_market.json \
  --universe all
```

Universe modes: `all`, `industry`, `concept`, `index`, `custom` (with `--symbols`).

Report `status` values:

- `ok` — screening completed
- `empty_universe` — legal empty pool after filters
- `data_error` — blocking data/universe error (exit code 1)

## After-close job (local)

```bash
tradingagents-scheduler after-close --trade-date 2026-06-19
tradingagents-scheduler status --trade-date 2026-06-19
```

Jobs are idempotent by `job_name + trade_date + config_hash`. A successful run is skipped unless `--force` is set. All attempts are retained under `~/.tradingagents/scheduler/runs/`.

Offline/dev with fixture-backed screening:

```bash
tradingagents-scheduler after-close \
  --trade-date 2026-01-02 \
  --fixture tests/fixtures/market_data/provider_mini.json
```

## Recovery

| Symptom | Action |
|---------|--------|
| `capability probe failed` | Set `TUSHARE_TOKEN`, run `tradingagents-market-data probe` |
| `daily completeness below threshold` | Re-run `sync --dataset daily`; check vendor coverage |
| `permission_denied` / rate limit | Wait and retry; do not disable quality gates |
| `current_only ... cannot be used historically` | Use `dc_member` snapshots or change universe |
| Stale published data | Failed sync does not replace last `PUBLISHED` version; fix and re-sync |

## Regression (developers)

```bash
PYTHONPATH=".pip_packages:." python3 -m pytest tests/ -q
PYTHONPATH=".pip_packages:." python3 -m ruff check tradingagents tests
```

Original single-stock CLI/Web/fixture flows remain available:

```bash
tradingagents --help
tradingagents-web
tradingagents-screen backtest-fixture --fixture tests/fixtures/screener/mvp_market.json
```
