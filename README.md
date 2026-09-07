# PulseX

PulseX is an automated news intelligence and publishing pipeline that surfaces important, understandable news from around the world — without turning the feed into noise.

It collects from trusted sources, verifies and classifies stories, clusters reports of the same event, prioritizes major developments, formats concise posts, and optionally publishes approved stories to X.

## Design goals

- **Broad coverage** — politics, conflict, disasters, finance, business, technology, cybersecurity, health, science, space, environment, crime, sports, and entertainment.
- **Disasters first** — verified severe disasters bypass normal category balancing and per-run limits.
- **One event, one post** — multiple outlets covering the same story are clustered instead of flooding the queue.
- **Real updates only** — genuine new facts trigger an update; wording tweaks and timestamp bumps do not.
- **Clean output** — boilerplate, malformed summaries, and low-quality posts are rejected before publishing.
- **Lean sources** — only high-value feeds are kept; redundant or low-yield ones are dropped.

## Pipeline

```
Trusted sources
  → Collection & normalization
  → Classification & corroboration
  → Event clustering & duplicate detection
  → Priority & major-disaster detection
  → Balanced queue selection
  → Formatting & quality checks
  → Media discovery
  → Production safety gates
  → Optional X publishing
```

## Sources

26 curated feeds, including BBC, Al Jazeera, DW, France 24, NPR, The Guardian, Africanews, UN News, USGS, NASA, ESA, WHO, SEC, CISA, and GDACS.

GDACS is filtered to Orange and Red alerts only, to keep disaster monitoring useful without low-severity noise.

Source configuration lives in [`config.json`](config.json).

## Priority & labeling

Importance and diversity are handled separately: a normal queue is balanced across topics and publishers, but a verified major disaster is never dropped to preserve that balance.

Major-event signals include verified emergency priority, authoritative disaster alerts, severe/extreme alert levels, large casualty or displacement counts, and strong markers like catastrophic flooding or declared emergencies.

Public labels:

| Label | Meaning |
|---|---|
| 🚨 `BREAKING` | Verified immediate event or major disaster |
| 🔴 `UPDATE` | Meaningful development to an event already tracked |
| 📰 `NEWS` / `DEVELOPING` | Important, non-breaking story |

## Duplicate & update detection

Story and event memory is stored in `data/news.db`:

| Memory | Retention | Purpose |
|---|---:|---|
| Individual stories | 48 hours | Avoids reprocessing seen articles |
| Normal events | 48 hours | Groups coverage of the same event |
| Major events | 7 days | Keeps high-impact events tracked longer |

Matching goes beyond exact headlines — normalized wording, event signals, numbers, context, and geography let differently phrased reports resolve to one event. Only one post per event is queued per run; a later cycle can still publish a genuine update.

## Quality controls

Before reaching production, posts are checked for: duplicate/repeated events, weak or unverified breaking claims, malformed or incomplete content, RSS/navigation boilerplate, concatenated or unrelated headlines, HTML/feed artifacts, low-confidence content, and formatting issues.

Verified major disasters get a narrowly relaxed minimum-length rule so a legitimate emergency isn't dropped for a short authoritative description — all other checks still apply.

## Media

Publisher images and MP4 video are discovered from RSS and Open Graph metadata.

- Public HTTP/HTTPS URLs only; private, localhost, link-local, and reserved targets are rejected.
- Media types are validated, download sizes capped, and redirects revalidated.
- Falls back to text-only if media is missing or invalid.
- Media discovery is optional — a story never fails purely for lacking media.

## Safety

- `X_PUBLISH_ENABLED` gates publishing; `X_KILL_SWITCH` can block it immediately.
- Health checks run before production.
- Posting is capped at 1 per rolling 30 minutes, 2 per rolling hour, and 48 per UTC day by default.
- Optional human review is supported.
- Production state is persisted separately from application source.
- Plain X browser-session JSON is never committed. GitHub Actions validates its structure/expiry locally, the live publisher confirms the session against X when a post is attempted and preserves refreshed cookies, then AES-256-GCM encrypts the session and stores only ciphertext on the `state` branch.
- An expired/revoked X session fails the live publish workflow visibly instead of being reported as a successful publish run.

## Scheduling

Triggered via GitHub Actions `workflow_dispatch`. For external scheduling, a service like **cron-job.org** can call the workflow-dispatch endpoint on a fixed interval — keep only one active scheduler to avoid duplicate runs.

```
cron-job.org (every 30 min) → GitHub workflow_dispatch → PulseX pipeline → Safety gates → Optional X publish
```

Workflow file: `.github/workflows/news.yml`

## GitHub Actions secrets

| Secret | Purpose |
|---|---|
| `X_WEB_SESSION` | Manually captured browser session used only as bootstrap/recovery |
| `X_SESSION_ENCRYPTION_KEY` | Long random secret used to encrypt/decrypt the rolling browser session |

The workflow prefers the encrypted rolling session from the `state` branch. Only ciphertext is stored there; the decryption key stays in GitHub Actions secrets. `X_WEB_SESSION` remains a recovery fallback if the rolling state is missing or cannot be decrypted. Plain session JSON exists only temporarily on the runner and is removed after the run.

If X explicitly revokes the account session, run `scripts/renew_x_session.sh`, then update `X_WEB_SESSION` with the command it prints and trigger one verification workflow.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```bash
.venv/bin/python -m src.main             # Collect, verify, prioritize, build queue
.venv/bin/python -m src.dry_run          # Preview output without posting
.venv/bin/python -m src.status           # Inspect current status
.venv/bin/python -m src.production_run   # Run production pipeline (requires publishing config)
```

## Tests

```bash
.venv/bin/python -m src.test_news_improvements
.venv/bin/python -m src.test_categories
.venv/bin/python -m src.regression_test
.venv/bin/python -m src.test_quality
.venv/bin/python -m src.test_publish_limits
.venv/bin/python -m src.test_session_crypto
.venv/bin/python -m src.test_x_session_health
.venv/bin/python -m src.test_production_run
```

Covers disaster prioritization, cross-source corroboration, same-event suppression, source filtering, feed cleanup, media handling, classification, and publishing fallbacks.

## Configuration

Runtime behavior is set in [`config.json`](config.json): minimum queue score, max ordinary stories per run, memory retention, breaking threshold, feed definitions, source tiers, disaster alert filtering, and collection limits.

Publishing is configured via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `X_PUBLISH_ENABLED` | `false` | Enables live publishing |
| `X_KILL_SWITCH` | `true` | Immediately blocks live publishing |
| `X_REQUIRE_HUMAN_REVIEW` | `true` | Requires manual approval |
| `X_POST_METHOD` | `web` | Publishing backend |
| `X_HEADLESS` | `true` | Browser visibility |
| `X_DAILY_POST_LIMIT` | `48` | Max posts per UTC day |
| `X_HALF_HOUR_POST_LIMIT` | `1` | Max posts per rolling 30 min |
| `X_HOURLY_POST_LIMIT` | `2` | Max posts per rolling 1 hour |
| `X_CDP_URL` | `http://localhost:9222` | Optional local CDP browser endpoint |

## Project structure

```
config.json                     Source and runtime configuration
.github/workflows/news.yml      GitHub Actions production workflow

src/
  collector.py                  Feed collection and normalization
  intelligence.py                Classification and corroboration
  emergency.py                  Verified major-disaster detection
  event_memory.py               Event clustering and update detection
  selection.py                  Balanced queue and disaster priority
  priority.py                   Importance scoring
  formatter.py                  Clean public post formatting
  quality.py                    Final content-quality checks
  media.py                      Safe image/video discovery and download
  production_controller.py      Production safety gate
  production_run.py             Production publish entry point
  x_session_health.py           Validates and refreshes the saved X session
  session_crypto.py             Encrypts/decrypts rolling X session state
  x_web_publisher.py            Saved-session browser publisher
  x_publisher.py                X publishing backend
  health_gate.py                Runtime health checks
  metrics.py                    Operational metrics
  status.py                     Current run status

data/
  news.db                       Story and event memory
  queue.json                    Current selected stories
  production_state.json         Publishing state
  source_health.json            Source health snapshot
```

PulseX is built to publish fewer, better stories: important events across sectors, strong disaster coverage, clean wording, and aggressive duplicate suppression.
