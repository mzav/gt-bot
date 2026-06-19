# Girl Talk Berlin – Meetings Telegram Bot

A Telegram bot for creating and managing community meetings for the GirlTalkBerlin (≈180 members).

This bot helps members to:
- Create a new meeting (topic, description, date & time, maximum participants, optional location, optional photo)
- Register/unregister for a meeting
- See all upcoming meetings
- See "my meetings" (as a host or participant)
- Receive notifications for upcoming meetings (3 days and 1 day before)
- Publish twice‑per‑month announcements to a separate GirlTalkAnnouncements Telegram channel
- Open a specific meeting directly from a channel announcement via a deep link

## Features
- Meeting lifecycle
  - Create meeting: topic, description, datetime, max participants, optional location, optional photo
  - Edit meeting details (host only)
  - Cancel meeting (host only)
  - Registration and waitlist when full
- Discovery
  - List of all upcoming meetings
  - List of "my meetings" if the user is host or is registered
  - Meeting deep links from channel announcements (`/start m_<token>`)
- Notifications
  - Automatic reminders for participants and hosts:
    - 3 days before event
    - 1 day before event
  - Host DM on participant signup/cancel: instant below threshold, batched digest above it
- Announcements
  - Twice per month, publish a digest/announcement post to a dedicated Telegram channel (GirlTalkAnnouncements)
  - Each announced meeting includes a CTA button linking to the bot with that meeting pre-selected
  - Spontaneous and same-day meeting posts also include the CTA button

## Tech stack
- python-telegram-bot (PTB) >= 21.5 — async Telegram Bot API framework
- SQLAlchemy >= 2.0 — ORM for persistence
- aiosqlite >= 0.19 — SQLite async driver
- APScheduler >= 3.10 — scheduling reminders and bi-monthly announcements
- python-dateutil >= 2.9 — robust date parsing/handling
- pydantic >= 2.7 — config and request validation
- python-dotenv >= 1.0 — local .env configuration
- tzdata >= 2025.1 — time zone data for consistent scheduling

## Repository layout
```
main.py              — entry point; wires config, DB, scheduler, and PTB lifecycle
requirements.txt     — Python dependencies
Dockerfile           — production image (Python 3.12-slim, Litestream binary)
railway.toml         — Railway deployment config
litestream.yml       — Litestream replication config (SQLite → Backblaze B2)
seed_dev.py          — dev seed script (populates local DB with test scenarios)
DECISIONS.md         — architecture and infrastructure decision log
FUTURE_ADJUSTMENTS.md— known follow-up items and open tech debt
bot/
  config.py          — Pydantic settings, loaded from env vars
  models.py          — SQLAlchemy models (User, Meeting, Registration)
  storage.py         — DB session and CRUD helpers
  handlers.py        — PTB command and callback handlers
  keyboards.py       — inline keyboard builders
  links.py           — meeting deep-link builders and /start payload parsing
  scheduler.py       — APScheduler jobs (reminders, announcements, host DMs)
  utils.py           — shared formatting helpers
tests/               — automated tests
```

## Quick start
1) Prerequisites
- Python 3.12+
- Telegram Bot token from @BotFather

2) Clone and set up a virtual environment
```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3) Create a .env file in the project root
```
# Telegram API
BOT_TOKEN=123456:ABC-YourBotTokenFromBotFather

# Bot username without @ — required in production for channel deep-link buttons
BOT_USERNAME=YourBotName

# Database (defaults to ./gtbot.db locally; /data/gtbot.db in production)
DATABASE_URL=sqlite+aiosqlite:///./gtbot.db

# Announcements channel (numeric ID, negative for channels)
ANNOUNCEMENTS_CHANNEL_ID=-1001234567890

# Timezone used for scheduling (IANA name)
TIMEZONE=Europe/Berlin

# Scheduling times for twice-monthly announcements (24h HH:MM, local TIMEZONE)
ANNOUNCE_DAYS=1,15
ANNOUNCE_TIME=10:00

# Daily reminder check time (HH:MM, local TIMEZONE)
DAILY_CHECK_TIME=09:00

# Host participant-change notifications:
# send instant DM when confirmed count < threshold, batch digest above it
NOTIFY_BATCH_THRESHOLD=10
NOTIFY_BATCH_INTERVAL_MINUTES=30

# Logging level
LOG_LEVEL=INFO
```

4) (Optional) Seed the local database with test scenarios
```
python seed_dev.py
```
Edit `MY_TELEGRAM_ID` and `MY_USERNAME` in `seed_dev.py` first so host-only buttons work when you chat with the bot.

5) Run the bot
```
python main.py
```

## Running tests

From the project root with the virtual environment activated:

```bash
pip install -r requirements.txt
PYTHONPATH=. pytest --cov=bot --cov=main --cov-report=term-missing
```

Coverage configuration lives in `.coveragerc` (`seed_dev.py` and `tests/` are omitted).

## Expected user commands and flows
- /start — Welcome and brief help
- /start m_<public_token> — Open a specific meeting (from a channel deep link); shows the same card and action buttons as `/upcoming_meetings` (register, leave, details, host actions)
- /help — Show available commands and usage
- /create_meeting — Create a new meeting via guided prompts:
  - Topic
  - Description
  - Date & time (e.g., 2025-09-12 18:30)
  - Max participants
  - Location (optional)
  - Photo (optional)
- /upcoming_meetings — List all upcoming meetings (paginated if needed)
- /my_meetings — Show meetings I host or I'm registered for
- /register <meeting_id> — Register for a specific meeting
- /unregister <meeting_id> — Cancel my registration
- /cancel <meeting_id> — Host cancels meeting
- /edit <meeting_id> — Host edits meeting details

Inline buttons are used for register/unregister, edit/cancel, and navigating lists.

## Meeting deep links

Each meeting has a stable `public_token` (12-character URL-safe string, unique). It is generated automatically on creation, backfilled for existing meetings at startup, and never changes when a meeting is edited.

**Link format:**
```
https://t.me/<BOT_USERNAME>?start=m_<public_token>
```

**Channel announcements** include a CTA built from this link:
- Twice-monthly digest — one packed message listing all meetings, each with an HTML deep link in the text
- Spontaneous new-meeting posts — HTML deep link in the post text (not an inline keyboard button)
- Same-day “meeting today” posts — HTML deep link in the post text

Channel posts use HTML links rather than inline URL buttons under the post, because `reply_markup` on a channel post replaces the “Leave a comment” button when the channel has a linked discussion group.

**Telegram Desktop quirk:** opening `t.me/<bot>?start=m_<token>` may show a START button and not send `/start` to the bot until the link is clicked again (or START is pressed). This is client-side behavior, not a bot bug. Mobile clients usually handle deep links more smoothly for users who have already started the bot.

**Bot behavior when a user opens the link:**
1. `/start` receives payload `m_<public_token>`
2. Bot looks up the meeting by `public_token` (not database id)
3. If available, shows the meeting card with contextual buttons (same as `/upcoming_meetings`)
4. If invalid, not found, canceled, or past — shows a friendly message and a button to list upcoming meetings

`BOT_USERNAME` should be set in production. If omitted, the bot resolves it via `getMe()` at startup; channel CTAs are skipped when no username is available.

## Data model
- User: id (Telegram user id), name, username
- Meeting: id, topic, description, start_at (timezone-aware UTC), max_participants, location, photo_file_id, public_token (unique, for deep links), created_by, created_at, updated_at, canceled_at (nullable)
- Registration: id, meeting_id, user_id, status (confirmed|waitlisted|canceled), created_at

## Notifications and scheduling
- Reminder jobs run daily at `DAILY_CHECK_TIME` and notify participants and host:
  - 3 days before start
  - 1 day before start
- Host participant-change DMs:
  - Instant DM when confirmed count < `NOTIFY_BATCH_THRESHOLD`
  - Batched digest (every `NOTIFY_BATCH_INTERVAL_MINUTES`) when at or above threshold
- Announcement jobs:
  - Twice per month on set days (e.g., 1 and 15) at `ANNOUNCE_TIME` in `TIMEZONE`
  - Post a digest of upcoming meetings to `ANNOUNCEMENTS_CHANNEL_ID` (one message, deep-link per meeting)
  - Daily job at `DAILY_CHECK_TIME` posts same-day meetings to the channel with CTA buttons

APScheduler uses `AsyncIOScheduler` with timezone from `TIMEZONE`. Jobs are rehydrated on startup from the database.

## Time zones
- Store datetimes in UTC in the database
- Parse user input in local `TIMEZONE` (Europe/Berlin by default), then convert to UTC
- Display formatted times in local `TIMEZONE` to users

## Deployment

The bot runs on [Railway](https://railway.app) via Docker. See `DECISIONS.md` for the full rationale.

- `Dockerfile` — two-stage build; Python 3.12-slim image
- `railway.toml` — sets builder to Dockerfile and restart policy to always
- Database file lives at `/data/gtbot.db` on a Railway persistent 1 GB volume
- [Litestream](https://litestream.io) streams SQLite WAL backups to Backblaze B2 continuously (see `litestream.yml`)

### Required env vars in production (Railway → Variables)
All `.env` vars above (including `BOT_USERNAME` when using channel announcements), plus Litestream backup vars:
```
LITESTREAM_BUCKET=your-b2-bucket-name
LITESTREAM_ENDPOINT=https://s3.us-west-002.backblazeb2.com
LITESTREAM_KEY_ID=your-b2-key-id
LITESTREAM_SECRET=your-b2-application-key
```

### Restore from backup
```
litestream restore -config litestream.yml /data/gtbot.db
```

## Security and moderation
- Only allow meeting cancel/edit for host
- Validate inputs with pydantic
- Deep-link payloads are treated as untrusted input; format is validated before DB lookup
- Public meeting tokens are opaque (no raw database ids in channel links or user-facing text)
- Registration and host permissions are unchanged — deep links cannot bypass existing checks
- Rate-limit heavy commands if needed

## Development roadmap
- v0: CRUD for meetings, registration, lists, reminders, edit/cancel ✓
- v0.1: Meeting deep links from channel announcements ✓
- v1: Waitlist auto-promotion, HTML escaping, global error handler
- v1.1: iCal export, richer formatting, pagination
- v2: Web admin dashboard (optional)

See `FUTURE_ADJUSTMENTS.md` for the current open items.

## Contributing
- Open issues for bugs/requests
- Use feature branches and pull requests
- Keep changes small and well described

## License
Specify a license of your choice (e.g., MIT). If none is specified yet, all rights reserved by default.

## Acknowledgements
- GirlTalkBerlin community
