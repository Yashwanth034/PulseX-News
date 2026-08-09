#!/bin/bash
# Auto bot: collect news, health check, post to X every 5 minutes.
cd "$(dirname "$0")" || exit 1

set -a
# shellcheck disable=SC1091
source .env
set +a

export X_PUBLISH_ENABLED="true"
export X_KILL_SWITCH="false"
export X_REQUIRE_HUMAN_REVIEW="false"
export X_HEADLESS="true"
export X_POST_METHOD="web"

LOG="data/bot_run.log"
STAMP=$(date -u +"%Y-%m-%d %H:%M:%S UTC")

{
  echo ""
  echo "=== $STAMP ==="
  .venv/bin/python -m src.main || echo "collect failed"
  .venv/bin/python -m src.health_gate || true
  .venv/bin/python -m src.production_run || echo "publish failed"
} >> "$LOG" 2>&1

tail -20 "$LOG"
