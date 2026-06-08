# Architecture & Infrastructure Decisions

Lightweight decision log for the Girl Talk Berlin bot.
Each entry records what was decided, why, and what was considered but rejected.

---

## 001 · Hosting platform — Railway

**Date:** 2026-05-17
**Status:** Decided

### Decision
Deploy the bot on [Railway](https://railway.app).

### Reasoning
- Zero ops setup: GitHub connect → auto-deploy on every push, no CLI or server config required.
- Built-in secrets UI, log viewer, one-click rollback.
- Persistent 1 GB volume for the SQLite file included.
- ~$5/mo — acceptable for a community project.
- Best fit for a single long-polling Python process at 150–200 users.

### Alternatives considered
| Option | Why rejected |
|---|---|
| Fly.io | Good option, slightly cheaper (~$3–4/mo), but requires flyctl + manual GitHub Action for CD. Extra setup for marginal saving. |
| Hetzner CX11 | Cheapest and most control, but requires Linux sysadmin work (Docker, systemd, firewall, manual secret rotation). Not worth it at this scale. |
| AWS / GCP | Overkill: IAM, VPC, billing alerts, 3–5× the cost. Makes sense only at >1k active users or if existing infra lives there. |

---

## 002 · Database — SQLite + Litestream

**Date:** 2026-05-17
**Status:** Decided

### Decision
Use SQLite (via SQLAlchemy 2 + aiosqlite) as the primary database, with [Litestream](https://litestream.io) streaming WAL backups to Backblaze B2.

### Reasoning
- The DB will stay under 5 MB for years at ~20 meetings/month with 5–10 registrations each.
- SQLite on a single process has zero operational overhead.
- Litestream provides continuous, point-in-time recovery without a managed DB.
- B2 cost at this data volume is effectively $0/mo.

### Alternatives considered
| Option | Why rejected |
|---|---|
| Postgres (Railway managed) | Adds ~$5–10/mo and operational complexity for a workload that will never stress SQLite. Revisit if multi-instance scaling becomes necessary. |

---

## 003 · AI integration — external LLM API

**Date:** 2026-05-17
**Status:** Planned

### Decision
Add AI features via an external LLM API (OpenAI / Anthropic / Gemini) rather than running a local model.

### Reasoning
- LLM calls are plain HTTPS requests — no infrastructure changes needed.
- Cost at this volume (150–200 users, low message frequency) is negligible (fractions of a cent per call).
- No GPU, no self-hosted model, no additional deployment surface.
- `litellm` recommended as the SDK wrapper to keep provider-switching easy.

### Constraints
- Add per-user rate limiting (`last_ai_call_at` DB column or in-memory dict) before launch to prevent accidental cost spikes.
- Store the API key as a Railway env var — never in `.env` committed to the repo.

---

## 004 · Backups — Litestream → Backblaze B2

**Date:** 2026-05-17
**Status:** Decided

### Decision
Run [Litestream](https://litestream.io) as a background process inside the Docker container. It streams the SQLite WAL to a private Backblaze B2 bucket continuously.

### Reasoning
- SQLite has no built-in replication; a Railway volume is ephemeral if the service is deleted or migrated.
- Litestream adds zero latency to bot reads/writes — it tails the WAL file asynchronously.
- B2 cost at this data volume (~5 MB DB) is effectively $0/mo.
- Recovery is a single command: `litestream restore -config litestream.yml /data/gtbot.db`.

### Implementation
- `Dockerfile`: copies the Litestream binary from `litestream/litestream:latest` and starts it with `litestream replicate -config /app/litestream.yml &` before `python main.py`.
- `litestream.yml`: watches `/data/gtbot.db`, replicates to the S3-compatible B2 endpoint using four env vars (`LITESTREAM_BUCKET`, `LITESTREAM_ENDPOINT`, `LITESTREAM_KEY_ID`, `LITESTREAM_SECRET`).

### Alternatives considered
| Option | Why rejected |
|---|---|
| Railway Postgres (managed) | Adds ~$5–10/mo and complexity for a workload that will never stress SQLite. |
| Scheduled `cp` to B2 via cron | Point-in-time only; would lose up to 24h of data on crash. Litestream is near-real-time. |
| No backups | Unacceptable — the DB is the only stateful part of the system. |

---

## 005 · Meeting deep links — public tokens

**Date:** 2026-06-06
**Status:** Decided

### Decision
Expose meetings in channel announcements via Telegram deep links using opaque `public_token` values, not database ids.

**Link format:** `https://t.me/<BOT_USERNAME>?start=m_<public_token>`

### Reasoning
- Channel posts are public; integer meeting ids are guessable and leak internal structure.
- Telegram `/start` payloads are limited to 64 characters; a 12-character URL-safe token plus `m_` prefix fits comfortably.
- Tokens are generated with `secrets` (stdlib), stored with a unique index, assigned on create, and backfilled at startup for existing rows.
- Tokens are stable across edits — links in old channel posts keep working until the meeting is canceled or passes.
- Reuses existing meeting list/detail UI and registration logic; deep link is only an entry path.

### Implementation notes
- `bot/links.py` — token generation, link builder, payload parser
- `BOT_USERNAME` env var; falls back to `bot.get_me().username` at startup
- Digest keeps one packed message with an HTML deep link per meeting; single-meeting posts use inline URL buttons
- Invalid, missing, canceled, or past meetings show a fallback message with a button to list upcoming meetings

### Alternatives considered
| Option | Why rejected |
|---|---|
| Numeric id in payload (`start=meet_123`) | Guessable; exposes internal ids in public URLs |
| UUID v4 (36 chars) | Works but longer than needed for this scale |
| Signed JWT payloads | Overkill; adds crypto dependency and expiry handling for a simple open-registration flow |

---

<!-- Add new entries above this line, incrementing the number. -->
