# Event data & enrichment quickstart (phase 5)

## Install

```bash
pip install -e ".[screener]"
```

Fixture/offline tests and tier **A** acceptance do not require `TUSHARE_TOKEN` or live network.

## What is covered

| Dataset | PIT level | Free primary source |
|---------|-----------|---------------------|
| `official_announcements` | `pit_required` | Sina `vCB_AllBulletin` |
| `event_news` | `best_effort` | Eastmoney np-weblist |
| `event_fund_flow` | `best_effort` | Eastmoney push2 |
| `event_hot_topics` | `current_only` | 10jqka hot topics |

Core screening only **requires** announcements when `require_announcements: true`.
News / fund-flow flags are optional and degrade gracefully.

Capability matrix: `docs/data/data-capability-matrix.yaml`.

## Sync announcements (free)

```bash
tradingagents-market-data init
tradingagents-market-data sync \
  --dataset announcements \
  --symbols 600000,000001 \
  --start 2026-06-01 \
  --end 2026-06-30 \
  --as-of 2026-06-20T15:30:00+08:00
```

CLI aliases: `events`, `market_events`, `official_announcements`.

JSON `status` values from sync:

| Status | Meaning |
|--------|---------|
| `published` | Bundle published (may be empty) |
| `error` | Network / parse / quality gate failure |
| `blocked` | Capability gate closed or dedup blocked |

### Empty semantics

- **`SUCCESS_EMPTY`** — provider returned no rows in range; a **published empty version** is created with coverage metadata (`symbols`, `start`, `end`).
- **Network errors** — never mapped to `SUCCESS_EMPTY`.
- **Historical screening** — empty sync must be **PIT-visible** at `signal_time` and cover all candidates plus the event query window (`max_event_age_days`).

Repeated identical empty syncs reuse the same `version_id` (stable coverage hash).

## Enable event enrichment in screening

`config/screener.example.yaml`:

```yaml
event_enrichment:
  enabled: true
  candidate_limit: 100      # only Top N from factor ranking are enriched
  max_event_age_days: 30
  event_weight: 0.20
  event_half_life_days: 7
  hard_risk_filter: true
  require_announcements: false
```

CLI override:

```bash
tradingagents-screen screen \
  --fixture tests/fixtures/screener/mvp_market.json \
  --universe all \
  --event-enrichment
```

Report fields when enabled:

- `base_ranking` — phase-4 factor order (unchanged)
- `event_ranking` — event-score order
- `enhanced_ranking` — fused order used for portfolio input
- `event_contributions` — per-symbol explainable items
- `event_dataset_versions` / `event_data_sources` — audit trail
- `risk_flags` / `event_degradations` — hard/soft handling

`status=data_error` when a required dataset is missing and cannot be satisfied by a PIT-valid empty sync.

## Offline fixtures

| Path | Purpose |
|------|---------|
| `tests/fixtures/events/provider_events.json` | Multi-scenario announcement fixture |
| `tests/fixtures/events/sina_bulletin_sample.html` | Parser contract sample |
| `tests/fixtures/events/recorded/` | Desensitized live probe metadata (Task 1) |
| `tests/fixtures/screener/mvp_market.json` | Screening + 5-day replay fixture |

Fixture scenarios include: positive/negative/neutral sentiment, revision chain, duplicate stable keys, `SUCCESS_EMPTY`, and `NETWORK_ERROR`.

## Acceptance tiers

Run the layered acceptance script (no embedded pytest):

```bash
# Tier A — offline fixture + recorded contract (default)
PYTHONPATH='.pip_packages:.' python3 scripts/accept_event_enrichment.py \
  --offline --recorded-contract

# Tier B — live Sina bulletin smoke (four boards)
PYTHONPATH='.pip_packages:.' python3 scripts/accept_event_enrichment.py \
  --live-smoke --home-dir /tmp/ta-accept-events
```

Exit codes:

| Code | Meaning |
|------|---------|
| `0` | `PASS` — all required steps in requested modes passed |
| `1` | `FAIL` — functional regression |
| `2` | `BLOCKED` — live smoke could not reach network (not a fake pass) |

JSON report includes: `status`, `tiers`, `dataset_versions`, `sources`, `pit_levels`, `request_count`, `duration_ms`, `memory_peak_mb`, per-step errors and degradations.

### Tier definitions

| Tier | Scope | Phase 5 requirement |
|------|-------|---------------------|
| **A** | Offline fixture + recorded contract | Must pass |
| **B** | Same-day free live bulletin smoke | Pass or explicit `BLOCKED` |
| **C** | Formal historical event backtest | Not in scope unless reliable history exists |
| **D** | Five consecutive trading-day operations | Phase 6 |

Tier A includes a **fixture 5-day replay** determinism check only; real five-day ops validation is phase 6.

## Recovery

| Symptom | Action |
|---------|--------|
| `required dataset official_announcements missing` | Sync announcements for candidates or disable `require_announcements` |
| Historical signal rejects future empty sync | Backdate is wrong — republish empty sync with `published_at <= signal_time` |
| `event_hot_topics` historical error | Expected (`current_only`); use live date or disable flag |
| Live acceptance `BLOCKED` | Check network/proxy; retry with `--network-mode system` |
| Duplicate empty versions | Should not happen after stable coverage hash; report issue if seen |

## Tests

```bash
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/events/ -q
PYTHONPATH='.pip_packages:.' python3 -m pytest tests/events/test_acceptance.py -q
```
