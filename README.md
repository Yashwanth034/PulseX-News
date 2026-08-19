# World News Bot

An automated news bot that collects headlines from RSS sources, filters and prioritizes them, and posts them to X (Twitter) — fully automatically, on a schedule.

## How it works

```
RSS sources → collect → verify/translate → qualify → prioritize
    → queue → human review (optional) → publish to X
```

1. **Collect** (`src/main.py`) — gathers news from dozens of validated RSS sources
2. **Verify & translate** (`src/intelligence.py`, `src/translator.py`) — classifies stories and translates non-English content to English
3. **Quality check** (`src/quality.py`) — filters out low-quality or duplicate stories
4. **Prioritize** (`src/priority.py`) — ranks stories by importance
5. **Queue** — ready stories land in `data/queue.json`
6. **Publish** (`src/production_run.py`) — posts stories to X with safety limits

## Posting to X

The bot can post using **three methods** (selected via `X_POST_METHOD`):

| Method | Description |
|---|---|
| `web` (default) | Browser automation through a **saved login session** — no API token needed |
| `cdp` | Connects to your own Chrome/Brave browser via remote debugging |
| `api` | Official X API v2 (requires a paid token) |

### One-time setup (web method)

1. Create `.env` in the project root:
   ```
   X_USERNAME="your_x_username"
   X_PASSWORD="your_x_password"
   ```
2. Log in once to capture the session:
   ```bash
   .venv/bin/python test_x_web.py manual
   ```
   A browser opens — log in manually. The session is saved to `data/web_session.json` and reused forever. **No repeated logins.**

3. Enable live publishing (see [Environment variables](#environment-variables)).

### One-time setup (cdp method)

Useful when X restricts automated logins:

1. Start your real browser with the debug port:
   ```bash
   brave-browser --remote-debugging-port=9222 &
   ```
2. Log in to x.com in that browser window.
3. Test posting:
   ```bash
   .venv/bin/python test_cdp.py post "hello"
   ```

### Posting methods compared

| | `web` | `cdp` | `api` |
|---|---|---|---|
| Cost | Free | Free | ~$100/month |
| Needs browser | No (saved session) | Yes (running) | No |
| Risk of login limits | Low | None | None |
| ToS risk | Yes | Yes | No |

> **Note:** Browser-based posting (web/cdp) is against X's Terms of Service and carries a risk of account suspension. The official API is the only fully compliant option. Use a throwaway account if possible.

## Safety features

The bot is deliberately conservative:

- **Dry run by default** — `X_PUBLISH_ENABLED` defaults to `false`, nothing ever contacts X
- **Kill switch** — `X_KILL_SWITCH` defaults to `true`, blocks all publishing
- **Daily limit** — max 40 posts per UTC day (`X_DAILY_POST_LIMIT`)
- **Half-hour limit** — max 3 posts per rolling 30 minutes (`X_HALF_HOUR_POST_LIMIT`)
- **Health gate** — RED health blocks publishing (`src/health_gate.py`)
- **Production controller** — final approval gate before any post (`src/production_controller.py`)
- **Human review** — optional approval layer (`src/review_cli.py`), on by default
- **Thread capacity** — a 3-tweet thread counts as 3 posts, checked before the first tweet is sent

## Memory & retention

The bot keeps two kinds of memory in `data/news.db`, cleaned automatically on every collection run:

| Memory | Retention | What it does |
|---|---|---|
| Individual stories (`stories` table) | 48 hours (`story_memory_hours`) | Prevents already-seen articles from being re-processed |
| Normal events (`events` table, `major=0`) | 48 hours (`event_memory_hours`) | Tracks how a story develops across sources |
| Major events (`events` table, `major=1`) | 168 hours / 7 days (`major_event_memory_hours`) | Keeps high-priority events in memory longer |

- **Automatic cleanup** — every run of `src.main` deletes only records whose retention has elapsed (timestamp-based, idempotent, safe in GitHub Actions)
- **Update detection** — if the same URL/title reappears with a newer published/updated timestamp, event memory re-examines it; meaningful changes become `UPDATE` events, mere timestamp bumps stay duplicates
- **Deduplication is never weakened** — same article with same/older timestamp is still skipped instantly

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `X_USERNAME` | — | X account username/email (in `.env`) |
| `X_PASSWORD` | — | X account password (in `.env`) |
| `X_OTP` | — | One-time verification code, if X asks |
| `X_HEADLESS` | `true` | `false` to watch the browser |
| `X_PUBLISH_ENABLED` | `false` | `true` to allow live publishing |
| `X_KILL_SWITCH` | `true` | `false` to disarm the kill switch |
| `X_REQUIRE_HUMAN_REVIEW` | `true` | `false` to skip the approval layer |
| `X_POST_METHOD` | `web` | `web`, `cdp`, or `api` |
| `X_DAILY_POST_LIMIT` | `40` | Max posts per UTC day |
| `X_HALF_HOUR_POST_LIMIT` | `3` | Max posts per rolling 30 min |
| `X_USER_ACCESS_TOKEN` | — | Official API bearer token (api method only) |
| `X_CDP_URL` | `http://localhost:9222` | CDP debug endpoint |

## Setup

Create the local virtual environment once and install all runtime/test dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Running

### Automatic (every 5 minutes)

```bash
crontab -e
# add:
*/5 * * * * /path/to/project/run_bot.sh >/dev/null 2>&1
```

`run_bot.sh` collects news, runs the health gate, then invokes the production runner and logs to `data/bot_run.log`. It no longer overrides safety settings. For unattended live posting, explicitly set `X_PUBLISH_ENABLED=true`, `X_KILL_SWITCH=false`, and (only if desired) `X_REQUIRE_HUMAN_REVIEW=false` in `.env`; otherwise publishing remains safely blocked.

### Manual

```bash
# collect news
.venv/bin/python -m src.main

# preview what would be posted (dry run)
.venv/bin/python -m src.dry_run

# publish (after enabling env vars)
.venv/bin/python -m src.production_run

# review a story (if human review is enabled)
.venv/bin/python -m src.review_cli STORY_ID APPROVE
```

### Tests

```bash
.venv/bin/python -m src.regression_test
.venv/bin/python -m src.test_quality
```

## GitHub Actions

The repository includes `.github/workflows/news.yml`, which runs the collection pipeline every 5 minutes in the cloud (collect, verify, quality, health gate, dashboard, metrics) and persists event memory to the `state` branch. Publishing is intentionally **not** done in CI — cloud runners cannot access your local browser session.

## Security

- Credentials live only in `.env`, which is **gitignored** — never committed to GitHub
- The saved browser session (`data/web_session.json`) is also gitignored
- Even with a public repository, no secrets reach the repo

## Project layout

```
src/
  collector.py            # RSS feed collection
  intelligence.py         # classification & verification
  translator.py           # translation to English
  quality.py              # quality filtering
  priority.py             # story ranking
  formatter.py            # tweet/thread formatting
  production_run.py       # publish entry point
  production_controller.py# final safety gate
  x_publisher.py          # official API publisher
  x_web_publisher.py      # browser-based publisher (saved session)
  health_gate.py          # system health status
  review_cli.py           # manual story review
  dashboard.py            # review dashboard
  metrics.py              # run metrics
  status.py               # run status
data/                     # queue, state, sessions, logs (gitignored where needed)
test_x_web.py             # login/session/post test tools
test_cdp.py               # CDP browser test tools
run_bot.sh                # cron entry point
```
