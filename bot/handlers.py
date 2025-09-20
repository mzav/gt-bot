"""Telegram command handlers and application builder for the bot."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from dateutil import parser, tz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
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
    # Conversation states
    STATE_TOPIC = 1
    STATE_DESCRIPTION = 2
    STATE_MAX = 3
    STATE_LOCATION = 4
    STATE_DATETIME = 5

    def __init__(self, settings: Settings, db: Database, scheduler: BotScheduler):
        self.settings = settings
        self.db = db
        self.scheduler = scheduler
        self.local_tz = tz.gettz(settings.tz)

    def cmd_create_meeting(self) -> ConversationHandler:
        return ConversationHandler(
            entry_points=[CommandHandler("create_meeting", self._create_meeting_start)],
            states={
                self.STATE_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._create_meeting_topic)],
                self.STATE_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._create_meeting_description)],
                self.STATE_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._create_meeting_max_members)],
                self.STATE_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._create_meeting_max_members)],
                self.STATE_DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._create_meeting_datetime)],
            },
            fallbacks=[CommandHandler("cancel", self._create_meeting_cancel)],
            allow_reentry=True,
        )

    def build(self) -> Application:
        """Create and configure the PTB Application with command handlers."""
        app = ApplicationBuilder().token(self.settings.telegram_bot_token).build()

        app.add_handler(CommandHandler(["start", "help"], self.cmd_start))
        app.add_handler(CommandHandler("upcoming_meetings", self.cmd_meetings))
        app.add_handler(CommandHandler("my_meetings", self.cmd_my))
        # Conversation handler for creating a meeting interactively in Russian
        app.add_handler(self.cmd_create_meeting())
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

    # ==== Conversation handlers for /create_meeting (Russian) ====
    async def _create_meeting_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return ConversationHandler.END
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        context.user_data.clear()
        await update.effective_message.reply_text(
            "Создаём новую встречу! Как она называется?"
        )
        return self.STATE_TOPIC

    async def _create_meeting_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        topic = (update.effective_message.text or "").strip()
        if not topic:
            await update.effective_message.reply_text("Пожалуйста, укажи название встречи.")
            return self.STATE_TOPIC
        context.user_data["topic"] = topic
        await update.effective_message.reply_text(
            f"Принято! Название встречи: ‘{topic}’.\nПожалуйста, напиши описание встречи"
        )
        return self.STATE_DESCRIPTION

    async def _create_meeting_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        desc = (update.effective_message.text or "").strip()
        if not desc:
            await update.effective_message.reply_text("Пожалуйста, напиши описание встречи")
            return self.STATE_DESCRIPTION
        context.user_data["description"] = desc
        await update.effective_message.reply_text(
            "Принято! Описание получено.\nПожалуйста, укажи максимальное количество участников для этой встречи.\nПример: 20"
        )
        return self.STATE_MAX

    async def _create_meeting_max_members(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.effective_message.text or "").strip()
        try:
            max_p = int(text)
            if max_p <= 0:
                raise ValueError
        except Exception:
            await update.effective_message.reply_text(
                "Не удалось распознать число. Пожалуйста, укажи максимальное количество участников. Пример: 20"
            )
            return self.STATE_MAX
        context.user_data["max_participants"] = max_p
        await update.effective_message.reply_text(
            f"Принято! Максимум участников: {max_p}.\nПожалуйста, укажи место проведения встречи (необязательно).\nПример: Cafe Circle Coffee"
        )
        return self.STATE_LOCATION

    async def _create_meeting_max_members(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = (update.effective_message.text or "").strip()
        skip_values = {"", "-", "—", "пропустить", "нет"}
        location = None if raw.lower() in skip_values else raw
        context.user_data["location"] = location
        received = "не указано" if not location else f"{location}"
        await update.effective_message.reply_text(
            f"Принято! Место проведения: {received}.\nПожалуйста, укажи дату и время начала по Берлину в следующем формате: дд.мм чч.мм"
        )
        return self.STATE_DATETIME

    async def _create_meeting_datetime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.effective_message.text or "").strip()
        parts = text.split()
        if len(parts) != 2:
            await update.effective_message.reply_text(
                "Неверный формат. Пожалуйста, укажи дату и время в формате: дд.мм чч.мм (например: 20.09 18.30)"
            )
            return self.STATE_DATETIME
        day_str, time_str = parts
        now_local = datetime.now(self.local_tz)
        try:
            # Compose with current year
            dt = datetime.strptime(f"{day_str} {time_str} {now_local.year}", "%d.%m %H.%M %Y")
        except Exception:
            await update.effective_message.reply_text(
                "Не удалось разобрать дату/время. Используй формат: дд.мм чч.мм (например: 20.09 18.30)"
            )
            return self.STATE_DATETIME
        # Attach local tz (Berlin) and convert to UTC
        local_dt = dt.replace(tzinfo=self.local_tz)
        start_utc = local_dt.astimezone(tz.UTC)

        # Collect data
        user = update.effective_user
        if not user:
            await update.effective_message.reply_text("Произошла ошибка. Попробуй ещё раз позже.")
            return ConversationHandler.END
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        topic = context.user_data.get("topic")
        description = context.user_data.get("description")
        max_participants = context.user_data.get("max_participants")
        location = context.user_data.get("location")

        meeting = await self.db.create_meeting(
            host_id=user.id,
            topic=topic,
            description=description,
            start_at_utc=start_utc,
            max_participants=max_participants,
            location=location,
        )
        self.scheduler.schedule_meeting_reminders(meeting)
        when_local = meeting.start_at_utc.astimezone(self.local_tz)
        summary = (
            "Спасибо! Встреча создана.\n\n"
            f"Название: {topic}\n"
            f"Описание: {description}\n"
            f"Дата и время (Берлин): {when_local:%d.%m %H:%M}\n"
            f"Место: {location or 'не указано'}\n"
            f"Максимум участников: {max_participants}\n"
            f"ID встречи: #{meeting.id}"
        )
        await update.effective_message.reply_text(summary)
        context.user_data.clear()
        return ConversationHandler.END

    async def _create_meeting_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.effective_message.reply_text("Создание встречи отменено.")
        return ConversationHandler.END

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
