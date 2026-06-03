"""Dev seed script — populates gtbot.db with test data covering all new feature scenarios.

Run once before starting the bot locally:
    source .venv/bin/activate
    python seed_dev.py

Your own Telegram user ID is injected as the host so all host-only buttons
work when you chat with the bot. Set MY_TELEGRAM_ID below.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone, timedelta

# ── CONFIGURE THIS ──────────────────────────────────────────────────────────
# Your own Telegram user ID (find it via @userinfobot on Telegram)
MY_TELEGRAM_ID = 0123456789          # <-- replace with your real ID
MY_NAME = "Your Name (host)"
MY_USERNAME = "yourusername"       # without @
# ────────────────────────────────────────────────────────────────────────────

from dotenv import load_dotenv
load_dotenv()

from bot.storage import Database
from bot.models import Registration, RegistrationStatus

DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./gtbot.db")


def _future(days: int, hour: int = 18) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


async def seed() -> None:
    if MY_TELEGRAM_ID == 0:
        print("❌  Set MY_TELEGRAM_ID in seed_dev.py before running.")
        return

    db = Database(DB_URL)
    await db.create_all()

    # ── Host (you) ──────────────────────────────────────────────────────────
    host = await db.get_or_create_user(MY_TELEGRAM_ID, MY_NAME, MY_USERNAME)
    print(f"✅ Host: {host.name} (id={host.id})")

    # ── Fake participants ────────────────────────────────────────────────────
    participants = [
        (1001, "Alice Müller",   "alice_m"),
        (1002, "Barbara Schmidt", "barb_s"),
        (1003, "Clara Bauer",    None),
        (1004, "Daria Kozlov",   "daria_k"),
        (1005, "Elena Fischer",  "elena_f"),
        (1006, "Fatima Hassan",  "fatima_h"),
        (1007, "Greta Wolff",    None),
        (1008, "Hana Novak",     "hana_n"),
        (1009, "Ines Roth",      "ines_r"),
        (1010, "Julia Wagner",   "julia_w"),
        (1011, "Katja Braun",    "katja_b"),
        (1012, "Lena Schulz",    "lena_s"),
    ]
    for uid, name, username in participants:
        await db.get_or_create_user(uid, name, username)
    print(f"✅ {len(participants)} fake participants created")

    # ── Scenario 1: Small meeting, 3 participants (instant notification zone)
    # Pressing "Участники" shows a short list.
    # Any new signup/cancel triggers an instant DM (threshold=10 by default).
    m1 = await db.create_meeting(
        host_id=host.id,
        topic="Small Meeting — 3 participants",
        description="Instant DM zone. Register a fake account to trigger instant host DM.",
        start_at_utc=_future(3),
        max_participants=15,
        location="Cafe Central, Berlin",
    )
    for uid, _, _ in participants[:3]:
        await db.register(m1.id, uid)
    print(f"✅ Meeting #{m1.id}: '{m1.topic}' — {3} participants")

    # ── Scenario 2: Large meeting, 11 participants (batched notification zone)
    # Further signups go to the batch queue; digest sent every 30 min.
    m2 = await db.create_meeting(
        host_id=host.id,
        topic="Large Meeting — 11 participants",
        description="Batch zone. Signups accumulate; digest sent on flush interval.",
        start_at_utc=_future(7),
        max_participants=20,
        location="Studio Nord, Berlin",
    )
    for uid, _, _ in participants[:11]:
        await db.register(m2.id, uid)
    print(f"✅ Meeting #{m2.id}: '{m2.topic}' — {11} participants")

    # ── Scenario 3: Empty meeting — tests empty participant list message
    m3 = await db.create_meeting(
        host_id=host.id,
        topic="Empty Meeting — no participants",
        description="No one signed up yet. Участники button shows the empty state.",
        start_at_utc=_future(14),
        max_participants=10,
        location=None,
    )
    print(f"✅ Meeting #{m3.id}: '{m3.topic}' — 0 participants")

    # ── Scenario 4: Full meeting — tests waitlist (waitlisted not shown in list)
    m4 = await db.create_meeting(
        host_id=host.id,
        topic="Full Meeting — waitlist present",
        description="Slots: 3. First 3 confirmed, rest waitlisted. List shows only confirmed.",
        start_at_utc=_future(10),
        max_participants=3,
        location="Zoom",
    )
    # max=3, host counts toward cap in register logic, so 2 slots for participants
    for uid, _, _ in participants[:4]:
        await db.register(m4.id, uid)
    print(f"✅ Meeting #{m4.id}: '{m4.topic}' — 2 confirmed, 2 waitlisted")

    # ── Scenario 5: Not-your-meeting — you should NOT see Участники button
    other_host_id = 2001
    await db.get_or_create_user(other_host_id, "Other Host", "otherhost")
    m5 = await db.create_meeting(
        host_id=other_host_id,
        topic="Someone Else's Meeting",
        description="You are not the host. No host buttons, no Участники.",
        start_at_utc=_future(5),
        max_participants=10,
        location="TBA",
    )
    print(f"✅ Meeting #{m5.id}: '{m5.topic}' — hosted by someone else")

    print("\n🎉 Seed complete. Start the bot and use /my_meetings or /upcoming_meetings.")
    print(f"\nScenario summary:")
    print(f"  #{m1.id}  Small (3 pax)   → instant DM on next signup/cancel")
    print(f"  #{m2.id}  Large (11 pax)  → batched DM (lower NOTIFY_BATCH_THRESHOLD to test faster)")
    print(f"  #{m3.id}  Empty           → empty-state participant list")
    print(f"  #{m4.id}  Full + waitlist → only confirmed shown in list")
    print(f"  #{m5.id}  Not your meeting→ no host buttons visible")


if __name__ == "__main__":
    asyncio.run(seed())
