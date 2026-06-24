# Stage 6A Paper Operations Quickstart

Stage 6A adds a paper portfolio ledger, T+1 execution, daily valuation, scheduler orchestration, CLI, and layered acceptance.

## Install

```bash
pip install -e ".[screener,dev]"
```

## Initialize

```bash
tradingagents-paper init --account-id demo --home-dir ~/.tradingagents
tradingagents-market-data init --home-dir ~/.tradingagents --provider fixture
```

## Daily workflow (offline fixture)

```bash
# After close: screen + plan (scheduler)
tradingagents-scheduler run-after-close \
  --trade-date 2026-01-06 \
  --account-id demo \
  --home-dir ~/.tradingagents \
  --fixture tests/fixtures/market_data/provider_mini.json

# Next morning: execute pending orders
tradingagents-scheduler run-open \
  --trade-date 2026-01-07 \
  --account-id demo \
  --home-dir ~/.tradingagents \
  --fixture tests/fixtures/paper/five_day_market.json

# End of day: corporate actions + valuation
tradingagents-paper close \
  --account-id demo \
  --trade-date 2026-01-06 \
  --home-dir ~/.tradingagents \
  --fixture tests/fixtures/paper/five_day_market.json

# Read-only status / reports
tradingagents-paper status --account-id demo --home-dir ~/.tradingagents
tradingagents-paper report \
  --account-id demo \
  --trade-date 2026-01-06 \
  --logical-run-key demo:2026-01-06:2026-01-07:five-day-uni:...:v1 \
  --revision 1 \
  --rebalance-run-id reb-...
```

## Acceptance

```bash
PYTHONPATH='.pip_packages:.' python3 scripts/accept_stage6a_paper.py --offline
PYTHONPATH='.pip_packages:.' python3 scripts/accept_stage6a_paper.py --live-smoke
PYTHONPATH='.pip_packages:.' python3 scripts/summarize_stage6a_observation.py ~/.tradingagents/observations
```

## Five-day replay fixture

Deterministic offline evidence lives in `tests/fixtures/paper/five_day_market.json`. Pytest covers replay and crash recovery:

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/paper/test_five_day_replay.py -q
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success / completed |
| 1 | Data or program error |
| 2 | Blocked (network/supplier) |
