# Stage 6A Final Acceptance Report

Date: 2026-06-22  
Scope: Paper portfolio operations (Tasks 0–8)

## Tier A — Offline

Run:

```bash
PYTHONPATH='.pip_packages:.' python3 scripts/accept_stage6a_paper.py --offline
```

Checks:

- DuckDB paper migrations apply cleanly
- Five-day deterministic replay (`tests/fixtures/paper/five_day_market.json`)
- Crash recovery on execution step matches clean replay fingerprint
- Atomic report revisions (`latest.json` pointer, no overwrite)
- Limit-up conservative buy rejection
- Missing close price rejected at valuation (no stale-price fallback)

Expected: JSON `"passed": true`, exit code `0`.

## Tier B — Live Smoke

Run:

```bash
PYTHONPATH='.pip_packages:.' python3 scripts/accept_stage6a_paper.py --live-smoke
```

Independent subprocess steps with timeouts:

- Market data init
- Paper account init
- Read-only status
- Scheduler open job (fixture-backed; may return blocked `2` without network)

Local ledger/invariant failures return `1`. External network blockage returns `2`.

## Tier C — Five Real Trading Days

Collect one signed manifest per real trading day, then:

```bash
PYTHONPATH='.pip_packages:.' python3 scripts/summarize_stage6a_observation.py <observation-dir>
```

Summarizer reports step success, durations, coverage, orders/fills/rejections, invariant checks, recovery count, manual intervention, and open defects. It does **not** claim PASS until five distinct open dates exist.

## Evidence artifacts

| Artifact | Location |
|----------|----------|
| Five-day fixture | `tests/fixtures/paper/five_day_market.json` |
| Replay engine | `tradingagents/paper/five_day_replay.py` |
| Offline acceptance | `tradingagents/paper/acceptance.py` |
| CLI | `tradingagents-paper` |
| Reports | `reports/paper/<account>/<date>/<logical_run_key>/rev-N/` |

## Known scope boundaries

- Task 8 does not claim Tier C PASS without real-market manifests
- Full screening pipeline is bypassed in five-day replay via frozen plans for determinism
- Operational five-day real-data collection remains a separate manual step
