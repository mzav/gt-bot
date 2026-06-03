# Girl Talk Berlin – Meetings Telegram Bot

A Telegram bot for creating and managing community meetings for the GirlTalkBerlin (≈180 members).

This bot helps members to:
- Create a new meeting (topic, description, date & time, maximum participants, optional location, optional photo)
- Register/unregister for a meeting
- See all upcoming meetings
- See "my meetings" (as a host or participant)
- Receive notifications for upcoming meetings (3 days and 1 day before)
- Publish twice‑per‑month announcements to a separate GirlTalkAnnouncements Telegram channel

## Features
- Meeting lifecycle
  - Create meeting: topic, description, datetime, max participants, optional location, optional photo
  - Edit meeting details (host only)
  - Cancel meeting (host only)
  - Registration and waitlist when full
- Discovery
  - List of all upcoming meetings
  - List of "my meetings" if the user is host or is registered
- Notifications
  - Automatic reminders for participants and hosts:
    - 3 days before event
    - 1 day before event
  - Host DM on participant signup/cancel: instant below threshold, batched digest above it
- Announcements
  - Twice per month, publish a digest/announcement post to a dedicated Telegram channel (GirlTalkAnnouncements)

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

## Expected user commands and flows
- /start — Welcome and brief help
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

## Data model
- User: id (Telegram user id), name, username
- Meeting: id, topic, description, start_at (timezone-aware UTC), max_participants, location, photo_file_id, created_by, created_at, updated_at, canceled_at (nullable)
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
  - Post a digest of upcoming meetings to `ANNOUNCEMENTS_CHANNEL_ID`

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
All `.env` vars above, plus Litestream backup vars:
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
- Rate-limit heavy commands if needed

## Development roadmap
- v0: CRUD for meetings, registration, lists, reminders, edit/cancel ✓
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
