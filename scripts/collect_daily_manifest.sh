#!/usr/bin/env bash
# Wrapper for collect_stage6a_daily_manifest.py (Tier C evidence).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/collect_stage6a_daily_manifest.py" "$@"
