"""Telegram command handlers and application builder for the bot."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from dateutil import parser, tz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

from .config import Settings
from .storage import Database
from .scheduler import BotScheduler

logger = logging.getLogger(__name__)


def _format_meeting_line(m, local_tz) -> str:
    """Format a one-line summary of a meeting in the given local timezone."""
    when_local = m.start_at_utc.astimezone(local_tz)
    return f"#{m.id} — {m.topic} — {when_local:%Y-%m-%d %H:%M} — {m.location or 'TBA'}"


def _parse_new_args(arg: str) -> Optional[dict]:
    """Parse /create_meeting command arguments separated by pipes.

    Expected format: "topic | description | YYYY-MM-DD HH:MM | max | location(optional)".
    Returns a dict on success or None if parsing fails.
    """
    # Expected: topic | description | YYYY-MM-DD HH:MM | max | location(optional)
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) < 4:
        return None
    topic, description, dt_str, max_str = parts[:4]
    location = parts[4] if len(parts) > 4 else None
    try:
        max_p = int(max_str)
    except Exception:
        return None
    return {"topic": topic, "description": description, "dt_str": dt_str, "max_participants": max_p, "location": location}


class BotApp:
    """Assembles the Telegram Application and command handlers."""
    def __init__(self, settings: Settings, db: Database, scheduler: BotScheduler):
        self.settings = settings
        self.db = db
        self.scheduler = scheduler
        self.local_tz = tz.gettz(settings.tz)

    def build(self) -> Application:
        """Create and configure the PTB Application with command handlers."""
        app = ApplicationBuilder().token(self.settings.telegram_bot_token).build()

        app.add_handler(CommandHandler(["start", "help"], self.cmd_start))
        app.add_handler(CommandHandler("upcoming_meetings", self.cmd_meetings))
        app.add_handler(CommandHandler("my_meetings", self.cmd_my))
        app.add_handler(CommandHandler("create_meeting", self.cmd_new))
        app.add_handler(CommandHandler("register", self.cmd_register))
        app.add_handler(CommandHandler("unregister", self.cmd_unregister))
        app.add_handler(CallbackQueryHandler(self.cb_register, pattern=r"^register:\d+$"))
        app.add_handler(CallbackQueryHandler(self.cb_details, pattern=r"^details:\d+$"))

        # Lifecycle hooks to start/stop scheduler
        async def on_start(_: Application) -> None:
            self.scheduler.start()

        async def on_stop(_: Application) -> None:
            self.scheduler.shutdown()

        app.post_init = on_start
        app.post_shutdown = on_stop
        return app

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start and /help: greet user and list commands."""
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        # TODO change welcome message
        msg = (
            "🌸 Добро пожаловать в GirlTalkBot! 🌸\n\n"
            "Я помогаю комьюнити Girl Talk легко создавать и вести встречи.\n\n"
            "Доступные команды:\n"
            "📝 /create_meeting — создать встречу topic | description | YYYY-MM-DD HH:MM | max | location\n"
            "📅 /upcoming_meetings — все события\n"
            "📗 /my_meetings — мои встречи\n"
            "❓ /help — показать помощь\n\n"
            "👉 Не забудь вступить в <a href='https://t.me/+AI-HCuAXy204NWQy'>канал с анонсами</a> 👈\n\n"
            "Оставить <a href='https://forms.gle/vVEt78wAvj38RrwQ7'>обратную связь</a> ✅\n\n"
            "Давай делать вместе организацию встреч проще! ✨"
        )
        await update.effective_message.reply_text(msg,
                                                  parse_mode=ParseMode.HTML,
                                                  disable_web_page_preview=True)

    async def cmd_meetings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        now_utc = datetime.utcnow().replace(tzinfo=tz.UTC)
        meetings = await self.db.list_upcoming_meetings(now_utc)
        if not meetings:
            await update.effective_message.reply_text("No upcoming meetings.")
            return
        # Send each meeting as a separate message with a register button
        for m in meetings:
            when_local = m.start_at_utc.astimezone(self.local_tz)
            host_name = await self.db.get_user_name(m.created_by) or "Unknown"
            confirmed = await self.db.count_confirmed(m.id)
            available = max(m.max_participants - confirmed, 0)
            text = (
                f"<b>{when_local:%Y-%m-%d %H:%M}</b>\n"
                f"<b>{m.topic}</b>\n"
                f"Ведет: {host_name}\n"
                f"Свободных мест: {available}"
            )
            keyboard = InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(text="Записаться", callback_data=f"register:{m.id}"),
                    InlineKeyboardButton(text="Подробности", callback_data=f"details:{m.id}")
                ]]
            )
            await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

    async def cmd_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        meetings = await self.db.list_user_meetings(user.id)
        if not meetings:
            await update.effective_message.reply_text("You have no meetings yet.")
            return
        for m in meetings:
            when_local = m.start_at_utc.astimezone(self.local_tz)
            host_name = await self.db.get_user_name(m.created_by) or "Unknown"
            confirmed = await self.db.count_confirmed(m.id)
            available = max(m.max_participants - confirmed, 0)
            text = (
                f"<b>{when_local:%Y-%m-%d %H:%M}</b>\n"
                f"<b>{m.topic}</b>\n"
                f"Ведет: {host_name}\n"
                f"Свободных мест: {available}"
            )
            keyboard = InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(text="Записаться", callback_data=f"register:{m.id}"),
                    InlineKeyboardButton(text="Подробности", callback_data=f"details:{m.id}")
                ]]
            )
            await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

    async def cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        if not context.args:
            await update.effective_message.reply_text(
                "Usage: /create_meeting topic | description | YYYY-MM-DD HH:MM | max | location(optional)"
            )
            return
        arg = " ".join(context.args)
        parsed = _parse_new_args(arg)
        if parsed is None:
            await update.effective_message.reply_text(
                "Could not parse your input. Make sure to use pipes '|' and a valid integer for max participants."
            )
            return
        # Parse datetime in local tz and convert to UTC
        try:
            local_dt = parser.parse(parsed["dt_str"])  # user-supplied local time
            if local_dt.tzinfo is None:
                local_dt = local_dt.replace(tzinfo=self.local_tz)
            start_utc = local_dt.astimezone(tz.UTC)
        except Exception:
            await update.effective_message.reply_text("Invalid date/time. Example: 2025-09-12 18:30")
            return
        meeting = await self.db.create_meeting(
            host_id=user.id,
            topic=parsed["topic"],
            description=parsed["description"],
            start_at_utc=start_utc,
            max_participants=parsed["max_participants"],
            location=parsed["location"],
        )
        # schedule reminders
        self.scheduler.schedule_meeting_reminders(meeting)
        when_local = meeting.start_at_utc.astimezone(self.local_tz)
        await update.effective_message.reply_text(
            f"Created meeting #{meeting.id}: {meeting.topic}\n"
            f"When: {when_local:%Y-%m-%d %H:%M}\n"
            f"Where: {meeting.location or 'TBA'}\n"
            f"Max: {meeting.max_participants}\n"
        )

    async def cmd_register(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        if not context.args:
            await update.effective_message.reply_text("Usage: /register <meeting_id>")
            return
        try:
            meeting_id = int(context.args[0])
        except Exception:
            await update.effective_message.reply_text("Meeting id must be a number.")
            return
        ok, msg = await self.db.register(meeting_id, user.id)
        await update.effective_message.reply_text(msg)

    async def cmd_unregister(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        if not context.args:
            await update.effective_message.reply_text("Usage: /unregister <meeting_id>")
            return
        try:
            meeting_id = int(context.args[0])
        except Exception:
            await update.effective_message.reply_text("Meeting id must be a number.")
            return
        ok, msg = await self.db.unregister(meeting_id, user.id)
        await update.effective_message.reply_text(msg)

    async def cb_register(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline 'Записаться' button presses to register the user."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        data = cq.data or ""
        try:
            _, meeting_id_str = data.split(":", 1)
            meeting_id = int(meeting_id_str)
        except Exception:
            await cq.message.reply_text("Invalid registration request.")
            return
        ok, msg = await self.db.register(meeting_id, user.id)
        await cq.message.reply_text(msg)

    async def cb_details(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline 'Подробности' button presses to show meeting details."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        data = cq.data or ""
        try:
            _, meeting_id_str = data.split(":", 1)
            meeting_id = int(meeting_id_str)
        except Exception:
            await cq.message.reply_text("Invalid details request.")
            return
        m = await self.db.get_meeting(meeting_id)
        if not m:
            await cq.message.reply_text("Meeting not found.")
            return
        when_local = m.start_at_utc.astimezone(self.local_tz)
        date_str = when_local.strftime("%A, %d %B %Y")
        time_str = when_local.strftime("%H:%M")
        host_user = await self.db.get_user(m.created_by)
        if host_user:
            host_display = host_user.name or (host_user.username or "Unknown")
            if host_user.username:
                host_display = f"{host_user.name} (@{host_user.username})" if host_user.name else f"@{host_user.username}"
        else:
            host_display = "Unknown"
        confirmed = await self.db.count_confirmed(m.id)
        details_text = (
            f"<b>{m.topic}</b>\n"
            f"📝  {m.description}\n\n\n"
            f"📅 Date: {date_str}\n"
            f"🕐 Time: {time_str} (Berlin time)\n"
            f"📍 Location {m.location or 'TBA'}\n"
            f"👤 Ведет: {host_display}\n"
            f"👥 Идет: {confirmed} / {m.max_participants} participants"
        )
        await cq.message.reply_text(details_text, parse_mode="HTML")
