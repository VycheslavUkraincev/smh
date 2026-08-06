#!/usr/bin/env bash
# Fail if public HTML promises instant SLA ("2 мин" / "2 min").
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if rg -n --ignore-case '2\s*мин|за\s*2\s*минут|in\s*2\s*minutes|ready\s*in\s*2\s*min' "$ROOT"/public/*.html; then
  echo "FAIL: instant-SLA language found in public HTML" >&2
  exit 1
fi
echo "OK: no instant-SLA regressions in public HTML"
