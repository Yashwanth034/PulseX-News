#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv/bin/python. Create/install the project environment first." >&2
  exit 1
fi

rm -f data/web_session.json

X_BROWSER_CHANNEL="${X_BROWSER_CHANNEL:-chrome}" \
X_HEADLESS=false \
.venv/bin/python test_x_web.py manual

.venv/bin/python - <<'PY'
import json
from pathlib import Path
p = Path("data/web_session.json")
if not p.exists() or not p.stat().st_size:
    raise SystemExit("Session capture did not produce data/web_session.json")
data = json.loads(p.read_text())
if not isinstance(data, dict) or not data.get("cookies"):
    raise SystemExit("Captured browser session is invalid")
print("Captured X session validated locally.")
PY

echo
echo "Now update the protected GitHub Actions secret without printing the session:"
echo "  gh secret set X_WEB_SESSION --repo Yashwanth034/PulseX-News < data/web_session.json"
echo "Then trigger one verification run:"
echo "  gh workflow run news.yml --repo Yashwanth034/PulseX-News"
