#!/bin/bash
# Auto bot: collect news, health check, post to X every 5 minutes.
cd "$(dirname "$0")" || exit 1

# Preserve explicit caller settings. They take precedence over .env, matching
# the Python publishers' environment-loading behavior.
X_RUNTIME_VARS=(
  X_PUBLISH_ENABLED
  X_KILL_SWITCH
  X_REQUIRE_HUMAN_REVIEW
  X_HEADLESS
  X_POST_METHOD
)
declare -A CALLER_X_VALUES=()
for name in "${X_RUNTIME_VARS[@]}"; do
  if [[ -v "$name" ]]; then
    CALLER_X_VALUES["$name"]="${!name}"
  fi
done

# Load optional local settings for values the caller did not explicitly set.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

for name in "${!CALLER_X_VALUES[@]}"; do
  printf -v "$name" '%s' "${CALLER_X_VALUES[$name]}"
  export "$name"
done
unset CALLER_X_VALUES X_RUNTIME_VARS name

# Publishing stays safe by default and must be explicitly enabled with
# X_PUBLISH_ENABLED=true and X_KILL_SWITCH=false.
export X_PUBLISH_ENABLED="${X_PUBLISH_ENABLED:-false}"
export X_KILL_SWITCH="${X_KILL_SWITCH:-true}"
export X_REQUIRE_HUMAN_REVIEW="${X_REQUIRE_HUMAN_REVIEW:-true}"
export X_HEADLESS="${X_HEADLESS:-true}"
export X_POST_METHOD="${X_POST_METHOD:-web}"

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
