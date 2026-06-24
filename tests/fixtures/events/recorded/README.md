# Recorded event provider responses (offline contract)

Phase 5 Task 1 freezes **metadata-only** recordings for capability probes.
Default tests run fully offline against this catalog and `docs/data/data-capability-matrix.yaml`.

## Rules

1. **Desensitize** all recordings: no cookies, tokens, emails, phone numbers, or full article bodies.
2. When license restricts redistribution, store only:
   - source id, endpoint, request shape
   - response SHA256
   - field presence checklist
   - license summary URL
3. Never save full news正文 unless explicitly permitted.
4. Compress raw JSON snapshots as `*.json.gz` when bodies are required for parser tests (Task 4+).

## Scenarios (required)

| Scenario | Purpose |
|---|---|
| `with_announcements` | Symbol with recent bulletin rows (`datelist` layout) |
| `no_announcements` | Legitimate `SUCCESS_EMPTY` (`暂时没有数据` message) |
| `revised_announcement` | Financial `report_list` revision / updated publish metadata |
| `pagination` | Page 2+ bulletin list differs from page 1 |
| `rate_limited` | Structured `RATE_LIMITED` (simulated in fixture) |
| `network_error` | Structured `NETWORK_ERROR` must not become empty |

## Boards (live probe 2026-06-20)

| Board | Symbol | Primary source |
|---|---|---|
| SSE main | 600000 | sina.corp.vCB_AllBulletin |
| SZSE main | 000001 | sina.corp.vCB_AllBulletin |
| ChiNext | 300001 | sina.corp.vCB_AllBulletin |
| STAR | 688001 | sina.corp.vCB_AllBulletin |

## Files

- `*_meta.json` — desensitized probe metadata and response hashes (committed)
- `*.json.gz` — optional raw bodies for parser tests (Task 4+, not required for Task 1)
