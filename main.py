"""Entry point for the Girl Talk Berlin Meetings Telegram bot.

This module wires configuration, database, scheduler, and the Telegram
Application lifecycle together.
"""
from __future__ import annotations

import asyncio
import logging
from logging import Logger

from bot.config import load_settings
from bot.storage import Database
from bot.scheduler import BotScheduler
from bot.handlers import BotApp


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

    bot_app = BotApp(settings, db, None)  # type: ignore[arg-type]

    # Create application first so we can send messages via scheduler
    application = bot_app.build()

    async def send_channel_message(channel_id: int, text: str) -> None:
        await application.bot.send_message(chat_id=channel_id, text=text)

    # Scheduler
    scheduler = BotScheduler(db=db, timezone=settings.tzinfo(), send_channel_message=send_channel_message)
    # Re-assign scheduler into bot_app instance so hooks work
    bot_app.scheduler = scheduler

    # Announcements schedule
    announce_conf = settings.announce_config()
    scheduler.schedule_announcements(announce_conf.days, announce_conf.time_of_day, settings.announcements_channel_id)

    # Explicit Application lifecycle to ensure an event loop exists (Python 3.12)
    await application.initialize()
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
