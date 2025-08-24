# Girl Talk Berlin – Meetings Telegram Bot

A Telegram bot for creating and managing community meetings for the GirlTalkBerlin (≈180 members).

This bot helps members to:
- Create a new meeting (topic, description, date & time, maximum participants, optional location)
- Register/unregister for a meeting
- See all upcoming meetings
- See "my meetings" (as a host or participant)
- Receive notifications for upcoming meetings (3 days and 1 day before)
- Publish twice‑per‑month announcements to a separate GirlTalkAnnouncements Telegram channel

## Features
- Meeting lifecycle
  - Create meeting: topic, description, datetime, max participants, optional location
  - Edit/cancel meeting by host or admins (planned)
  - Registration and waitlist when full (planned)
- Discovery
  - List of all upcoming meetings
  - List of "my meetings" if the user is host or is registered
- Notifications
  - Automatic reminders for participants and hosts:
    - 3 days before event
    - 1 day before event
- Announcements
  - Twice per month, publish a digest/announcement post to a dedicated Telegram channel (GirlTalkAnnouncements)

## Tech stack (as defined in requirements)
- python-telegram-bot (PTB) >= 21.5 — async Telegram Bot API framework
- SQLAlchemy >= 2.0 — ORM for persistence
- aiosqlite >= 0.19 — SQLite async driver
- APScheduler >= 3.10 — scheduling reminders and bi-monthly announcements
- python-dateutil >= 2.9 — robust date parsing/handling
- pydantic >= 2.7 — config and request validation
- python-dotenv >= 1.0 — local .env configuration
- TIMEZONEdata >= 2025.1 — time zone data for consistent scheduling

## Repository layout
- main.py — entry point (placeholder currently)
- requirements.txt — Python dependencies

Additional modules (to be implemented) are expected:
- bot/handlers.py — command and callback handlers
- bot/models.py — SQLAlchemy models (Meeting, User, Registration, etc.)
- bot/storage.py — DB session and CRUD utils
- bot/scheduler.py — APScheduler jobs (reminders, announcements)
- bot/config.py — Pydantic settings

## Quick start
1) Prerequisites
- Python 3.12+ recommended
- Telegram Bot token from @BotFather

2) Clone and set up a virtual environment
```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3) Create a .env file in the project root
Use this template and adjust values:
```
# Telegram API
BOT_TOKEN=123456:ABC-YourBotTokenFromBotFather

# Announcements channel (numeric ID, negative for channels)
ANNOUNCEMENTS_CHANNEL_ID=-1001234567890

# Timezone used for scheduling (IANA name)
TIMEZONE=Europe/Berlin

# Scheduling times for twice-monthly announcements (24h HH:MM, local TIMEZONE)
ANNOUNCE_DAYS=1,15
ANNOUNCE_TIME=10:00

# logging level
LOG_LEVEL=INFO
```

4) Run the bot
```
python main.py
```

## Expected user commands and flows
These commands define the UX contract for the implementation:
- /start — Welcome and brief help
- /help — Show available commands and usage
- /create_meeting — Create a new meeting via guided prompts:
  - Topic
  - Description
  - Date & time (e.g., 2025-09-12 18:30)
  - Max participants
  - Location (optional)
- /upcoming_meetings — List all upcoming meetings (paginated if needed)
- /my_meetings — Show meetings I host or I’m registered for
- /register <meeting_id> — Register for a specific meeting
- /unregister <meeting_id> — Cancel my registration
- /cancel <meeting_id> — Host/admin cancels meeting (planned)
- /edit <meeting_id> — Host/admin edits details (planned)

Inline buttons are recommended for register/unregister and for navigating lists.

## Data model (proposed)
- User: id (Telegram user id), name, username
- Meeting: id, topic, description, start_at (timezone-aware), max_participants, location, created_by, created_at, updated_at, canceled_at (nullable)
- Registration: id, meeting_id, user_id, status (confirmed|waitlisted|canceled), created_at

## Notifications and scheduling
- Reminder jobs scheduled at meeting creation time to notify:
  - 3 days before start
  - 1 day before start
- Reminder target audience:
  - Registered participants and the host
- Announcement jobs:
  - Twice per month on set days (e.g., 1 and 15) at ANNOUNCE_TIME in TIMEZONE
  - Post a digest of upcoming meetings to ANNOUNCEMENTS_CHANNEL_ID

APScheduler recommendation:
- Use AsyncIOScheduler with timezone from TIMEZONE
- Persist jobs or rehydrate on startup from the database

## Time zones
- Store datetimes in UTC in the database
- Parse user input in local TIMEZONE (Europe/Berlin by default), then convert to UTC
- Display formatted times in local TIMEZONE to users

## Running locally
- Ensure .env is configured
- Activate venv and run: `python main.py`
- Use a private test group or direct messages for development

## Deployment notes
- Any environment that can run Python 3.11+
- Provide persistent storage for the SQLite file or switch to a server DB (e.g., Postgres via asyncpg)
- Ensure the process manager restarts the bot on failure (systemd, Docker, Supervisor, etc.)
- Timezone must be correctly set for deterministic scheduling

### Docker (optional pattern)
- Build an image and pass environment variables at runtime
- Mount a volume for the database file if using SQLite

## Security and moderation
- Only allow meeting cancel/edit for host and admins
- Validate inputs rigorously (pydantic)
- Rate-limit heavy commands if needed

## Development roadmap
- v0: CRUD for meetings, registration, lists, reminders ✓ (target)
- v1: Waitlist auto-promotion, editing, cancelation with reason
- v1.1: iCal export, richer formatting, pagination
- v2: Web admin dashboard (optional)

## Contributing
- Open issues for bugs/requests
- Use feature branches and pull requests
- Keep changes small and well described

## License
Specify a license of your choice (e.g., MIT). If none is specified yet, all rights reserved by default.

## Acknowledgements
- GirlTalkBerlin community
