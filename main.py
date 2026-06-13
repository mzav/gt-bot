"""Entry point for the Girl Talk Berlin Meetings Telegram bot.

This module wires configuration, database, scheduler, and the Telegram
Application lifecycle together.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from logging import Logger

from dateutil import tz

from bot.config import load_settings
from bot.storage import Database
from bot.scheduler import BotScheduler
from bot.handlers import BotApp
from bot.waitlist import WaitlistService


_CAPTION_LIMIT = 1024


async def _run_migrations(engine, db: Database) -> None:
    """Add columns introduced after initial schema creation."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    async with engine.connect() as conn:
        try:
            await conn.execute(text("ALTER TABLE meetings ADD COLUMN photo_file_id VARCHAR(255)"))
            await conn.commit()
        except OperationalError:
            pass  # column already exists
        try:
            await conn.execute(text("ALTER TABLE meetings ADD COLUMN public_token VARCHAR(64)"))
            await conn.commit()
        except OperationalError:
            pass  # column already exists
        try:
            await conn.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS ix_meetings_public_token ON meetings(public_token)")
            )
            await conn.commit()
        except OperationalError:
            pass
        try:
            await conn.execute(text("ALTER TABLE meetings ADD COLUMN end_at_utc DATETIME"))
            await conn.commit()
        except OperationalError:
            pass
        try:
            await conn.execute(text("ALTER TABLE meetings ADD COLUMN registration_starts_at_utc DATETIME"))
            await conn.commit()
        except OperationalError:
            pass

    backfilled = await db.backfill_meeting_public_tokens()
    if backfilled:
        log = logging.getLogger("gt-bot")
        log.info("Backfilled public_token for %d meeting(s)", backfilled)


async def main_async() -> None:
    """Run the bot in an asyncio event loop.

    Initializes configuration, database schema, scheduler, builds the Telegram
    Application and starts long polling. Blocks until interrupted, then shuts
    down gracefully.
    """
    settings = load_settings()

    # Logging
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format='[%(asctime)s] %(levelname)s %(name)s: %(message)s')
    log: Logger = logging.getLogger("gt-bot")

    # Database
    db = Database(settings.database_url)
    await db.create_all()
    await _run_migrations(db.engine, db)

    waitlist = WaitlistService(
        db,
        offer_ttl=timedelta(hours=settings.waitlist_offer_expiry_hours),
        local_tz=tz.gettz(settings.tz),
    )

    bot_app = BotApp(settings, db, None, waitlist)  # type: ignore[arg-type]

    # Create application first so we can send messages via scheduler
    application = bot_app.build()

    await application.initialize()
    bot_username = settings.telegram_bot_username
    if not bot_username:
        me = await application.bot.get_me()
        bot_username = me.username
    if settings.announcements_channel_id and not bot_username:
        log.error("BOT_USERNAME is required for meeting deep links in channel announcements")
    bot_app.bot_username = bot_username

    async def send_channel_card(
        channel_id: int,
        text: str,
        photo_file_id: str | None = None,
        *,
        reply_markup=None,
    ) -> None:
        if photo_file_id:
            if len(text) <= _CAPTION_LIMIT:
                await application.bot.send_photo(
                    chat_id=channel_id,
                    photo=photo_file_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
            else:
                await application.bot.send_photo(chat_id=channel_id, photo=photo_file_id, reply_markup=reply_markup)
                await application.bot.send_message(
                    chat_id=channel_id, text=text, parse_mode="HTML", reply_markup=reply_markup
                )
        else:
            await application.bot.send_message(
                chat_id=channel_id, text=text, parse_mode="HTML", reply_markup=reply_markup
            )

    # Scheduler (bot passed for host DM notifications)
    scheduler = BotScheduler(
        db=db,
        timezone=settings.tzinfo(),
        send_channel_card=send_channel_card,
        bot=application.bot,
        notify_threshold=settings.notify_batch_threshold,
        bot_username=bot_username,
        waitlist_service=waitlist,
    )
    # Re-assign scheduler into bot_app instance so hooks work
    bot_app.scheduler = scheduler

    # Announcements schedule
    announce_conf = settings.announce_config()
    scheduler.schedule_announcements(announce_conf.days, announce_conf.time_of_day, settings.announcements_channel_id)
    scheduler.schedule_daily_reminders(settings.daily_check_time_parsed(), settings.announcements_channel_id)
    scheduler.schedule_host_notifications(settings.notify_batch_interval_minutes)
    scheduler.schedule_waitlist_expiration(settings.waitlist_offer_check_interval_minutes)

    try:
        # Ensure scheduler is running even if post_init hook changes in PTB
        scheduler.start()
        await application.start()
        await application.updater.start_polling()
        # Block until interrupted (Ctrl+C) or process exit
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
    finally:
        # Ensure updater is stopped before shutting down the application
        try:
            await application.updater.stop()
        except Exception:
            pass
        await application.stop()
        await application.shutdown()
        scheduler.shutdown()


def main() -> None:
    """Synchronous entrypoint delegating to the async main.

    Uses asyncio.run to manage the event loop.
    """
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
