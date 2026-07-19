"""HTTP server for the community iCalendar subscription feed."""
from __future__ import annotations

import logging
import secrets
from typing import TYPE_CHECKING

from aiohttp import web

from .ical_feed import build_community_ical_feed
from .utils import utc_now

if TYPE_CHECKING:
    from .config import Settings
    from .storage import Database

logger = logging.getLogger("gt-bot.calendar_feed")

_ICAL_CONTENT_TYPE = "text/calendar"


def create_calendar_feed_app(
    *,
    db: Database,
    settings: Settings,
    bot_username: str | None,
) -> web.Application:
    """Create an aiohttp application serving the community calendar feed."""
    expected_token = settings.calendar_feed_token or ""

    async def handle_feed(request: web.Request) -> web.Response:
        provided = request.match_info.get("token", "")
        if not expected_token or not secrets.compare_digest(provided, expected_token):
            logger.warning("calendar_feed_unauthorized")
            raise web.HTTPNotFound()

        try:
            now = utc_now()
            meetings = await db.list_upcoming_meetings(now)
            body = build_community_ical_feed(
                meetings,
                bot_username=bot_username,
                now_utc=now,
            )
        except Exception:
            logger.exception("calendar_feed_error")
            raise web.HTTPInternalServerError()

        return web.Response(
            text=body,
            content_type=_ICAL_CONTENT_TYPE,
            charset="utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    app = web.Application()
    app.router.add_get("/calendar/community/{token}.ics", handle_feed)
    return app


async def start_calendar_feed_server(
    *,
    db: Database,
    settings: Settings,
    bot_username: str | None,
) -> web.AppRunner:
    """Start the calendar feed HTTP server and return its runner."""
    app = create_calendar_feed_app(db=db, settings=settings, bot_username=bot_username)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.calendar_feed_port)
    await site.start()
    logger.info("Calendar feed server listening on port %d", settings.calendar_feed_port)
    return runner


async def stop_calendar_feed_server(runner: web.AppRunner | None) -> None:
    """Shut down the calendar feed HTTP server."""
    if runner is not None:
        await runner.cleanup()
