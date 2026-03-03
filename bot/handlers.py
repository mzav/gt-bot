"""Telegram command handlers and application builder for the bot."""
from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from typing import Optional

from dateutil import tz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

from .config import Settings
from .storage import Database
from .scheduler import BotScheduler
from .utils import ensure_utc
from .keyboards import (
    MeetingCalendar,
    MonthPickerKeyboard,
    TimePickerKeyboard,
    LSTEP_TRANSLATIONS,
)

logger = logging.getLogger(__name__)


def _format_meeting_line(m, local_tz) -> str:
    """Format a one-line summary of a meeting in the given local timezone."""
    when_local = ensure_utc(m.start_at_utc).astimezone(local_tz)
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
    return {
        "topic": topic,
        "description": description,
        "dt_str": dt_str,
        "max_participants": max_p,
        "location": location,
    }


class BotApp:
    """Assembles the Telegram Application and command handlers."""

    # Conversation states for the create_meeting flow
    STATE_TOPIC = 1
    STATE_DESCRIPTION = 2
    STATE_MAX = 3
    STATE_LOCATION = 4
    STATE_MONTH = 5
    STATE_DATE = 6
    STATE_HOUR = 7
    STATE_MINUTE = 8

    # Conversation states for the edit_meeting flow
    STATE_EDIT_MENU = 10
    STATE_EDIT_TOPIC = 11
    STATE_EDIT_DESCRIPTION = 12
    STATE_EDIT_MAX = 13
    STATE_EDIT_LOCATION = 14
    STATE_EDIT_MONTH = 15
    STATE_EDIT_DATE = 16
    STATE_EDIT_HOUR = 17
    STATE_EDIT_MINUTE = 18

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
        app.add_handler(self._build_create_meeting_handler())
        app.add_handler(CommandHandler("register", self.cmd_register))
        app.add_handler(CommandHandler("unregister", self.cmd_unregister))
        app.add_handler(CallbackQueryHandler(self.cb_register, pattern=r"^register:\d+$"))
        app.add_handler(CallbackQueryHandler(self.cb_details, pattern=r"^details:\d+$"))
        app.add_handler(self._build_edit_meeting_handler())
        app.add_handler(CallbackQueryHandler(self.cb_cancel_meeting, pattern=r"^cancel:\d+$"))
        app.add_handler(CallbackQueryHandler(self.cb_cancel_confirm, pattern=r"^cancel_confirm:\d+$"))
        app.add_handler(CallbackQueryHandler(self.cb_cancel_abort, pattern=r"^cancel_abort:\d+$"))

        # Lifecycle hooks to start/stop scheduler
        async def on_start(_: Application) -> None:
            self.scheduler.start()

        async def on_stop(_: Application) -> None:
            self.scheduler.shutdown()

        app.post_init = on_start
        app.post_shutdown = on_stop
        return app

    def _build_create_meeting_handler(self) -> ConversationHandler:
        """Build the conversation handler for interactive meeting creation."""
        return ConversationHandler(
            entry_points=[CommandHandler("create_meeting", self._create_meeting_start)],
            states={
                self.STATE_TOPIC: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._create_meeting_topic)
                ],
                self.STATE_DESCRIPTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._create_meeting_description)
                ],
                self.STATE_MAX: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._create_meeting_max_members)
                ],
                self.STATE_LOCATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._create_meeting_location)
                ],
                self.STATE_MONTH: [
                    CallbackQueryHandler(self._create_meeting_month_callback, pattern=r"^month:")
                ],
                self.STATE_DATE: [
                    CallbackQueryHandler(self._create_meeting_calendar_callback, pattern=r"^cbcal_")
                ],
                self.STATE_HOUR: [
                    CallbackQueryHandler(self._create_meeting_hour_callback, pattern=r"^hour:")
                ],
                self.STATE_MINUTE: [
                    CallbackQueryHandler(self._create_meeting_minute_callback, pattern=r"^time:"),
                    CallbackQueryHandler(self._create_meeting_minute_callback, pattern=r"^hour:back$"),
                ],
            },
            fallbacks=[CommandHandler("cancel", self._create_meeting_cancel)],
            allow_reentry=True,
        )

    def _build_edit_meeting_handler(self) -> ConversationHandler:
        """Build the conversation handler for editing a meeting."""
        return ConversationHandler(
            entry_points=[CallbackQueryHandler(self._edit_meeting_start, pattern=r"^edit:\d+$")],
            states={
                self.STATE_EDIT_MENU: [
                    CallbackQueryHandler(self._edit_select_topic, pattern=r"^edit_field:topic$"),
                    CallbackQueryHandler(self._edit_select_description, pattern=r"^edit_field:description$"),
                    CallbackQueryHandler(self._edit_select_max, pattern=r"^edit_field:max$"),
                    CallbackQueryHandler(self._edit_select_location, pattern=r"^edit_field:location$"),
                    CallbackQueryHandler(self._edit_select_datetime, pattern=r"^edit_field:datetime$"),
                    CallbackQueryHandler(self._edit_back_to_menu, pattern=r"^edit_field:back$"),
                    CallbackQueryHandler(self._edit_done, pattern=r"^edit_field:done$"),
                ],
                self.STATE_EDIT_TOPIC: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._edit_topic_handler)
                ],
                self.STATE_EDIT_DESCRIPTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._edit_description_handler)
                ],
                self.STATE_EDIT_MAX: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._edit_max_handler)
                ],
                self.STATE_EDIT_LOCATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self._edit_location_handler)
                ],
                self.STATE_EDIT_MONTH: [
                    CallbackQueryHandler(self._edit_month_callback, pattern=r"^month:")
                ],
                self.STATE_EDIT_DATE: [
                    CallbackQueryHandler(self._edit_calendar_callback, pattern=r"^cbcal_")
                ],
                self.STATE_EDIT_HOUR: [
                    CallbackQueryHandler(self._edit_hour_callback, pattern=r"^hour:")
                ],
                self.STATE_EDIT_MINUTE: [
                    CallbackQueryHandler(self._edit_minute_callback, pattern=r"^time:"),
                    CallbackQueryHandler(self._edit_minute_callback, pattern=r"^hour:back$"),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", self._edit_cancel),
                CallbackQueryHandler(self._edit_done, pattern=r"^edit_field:done$"),
            ],
            allow_reentry=True,
            per_message=False,
        )

    def _build_edit_menu_keyboard(self) -> InlineKeyboardMarkup:
        """Build the inline keyboard for selecting which field to edit."""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(text="Тему", callback_data="edit_field:topic"),
                InlineKeyboardButton(text="Описание", callback_data="edit_field:description"),
            ],
            [
                InlineKeyboardButton(text="Дату и время", callback_data="edit_field:datetime"),
            ],
            [
                InlineKeyboardButton(text="Макс. участников", callback_data="edit_field:max"),
                InlineKeyboardButton(text="Место", callback_data="edit_field:location"),
            ],
            [
                InlineKeyboardButton(text="✅ Готово", callback_data="edit_field:done"),
            ],
        ])

    # ========== Edit Meeting Conversation Handlers ==========

    async def _edit_meeting_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Entry point: show the edit menu when user clicks 'Изменить' button."""
        cq = update.callback_query
        if not cq:
            return ConversationHandler.END
        await cq.answer()

        user = update.effective_user
        if not user:
            return ConversationHandler.END

        data = cq.data or ""
        try:
            _, meeting_id_str = data.split(":", 1)
            meeting_id = int(meeting_id_str)
        except Exception:
            await cq.message.reply_text("Ошибка: неверный запрос.")
            return ConversationHandler.END

        meeting = await self.db.get_meeting(meeting_id)
        if not meeting:
            await cq.message.reply_text("Встреча не найдена.")
            return ConversationHandler.END

        if meeting.created_by != user.id:
            await cq.message.reply_text("Только автор встречи может её редактировать.")
            return ConversationHandler.END

        context.user_data["edit_meeting_id"] = meeting_id
        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)

        text = (
            f"✏️ <b>Редактирование встречи #{meeting_id}</b>\n\n"
            f"📌 Тема: {meeting.topic}\n"
            f"📝 Описание: {meeting.description}\n"
            f"📅 Дата и время: {when_local:%d.%m.%Y %H:%M}\n"
            f"👥 Макс. участников: {meeting.max_participants}\n"
            f"📍 Место: {meeting.location or 'не указано'}\n\n"
            "Выбери, что хочешь изменить:"
        )
        await cq.message.reply_text(
            text,
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML"
        )
        return self.STATE_EDIT_MENU

    async def _edit_back_to_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Return to the edit menu."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()

        meeting_id = context.user_data.get("edit_meeting_id")
        if not meeting_id:
            await cq.message.reply_text("Сессия редактирования истекла. Начни заново.")
            return ConversationHandler.END

        meeting = await self.db.get_meeting(meeting_id)
        if not meeting:
            await cq.message.reply_text("Встреча не найдена.")
            return ConversationHandler.END

        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        text = (
            f"✏️ <b>Редактирование встречи #{meeting_id}</b>\n\n"
            f"📌 Тема: {meeting.topic}\n"
            f"📝 Описание: {meeting.description}\n"
            f"📅 Дата и время: {when_local:%d.%m.%Y %H:%M}\n"
            f"👥 Макс. участников: {meeting.max_participants}\n"
            f"📍 Место: {meeting.location or 'не указано'}\n\n"
            "Выбери, что хочешь изменить:"
        )
        await cq.edit_message_text(
            text,
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML"
        )
        return self.STATE_EDIT_MENU

    async def _edit_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Finish editing and show confirmation."""
        cq = update.callback_query
        if cq:
            await cq.answer()

        meeting_id = context.user_data.get("edit_meeting_id")
        if meeting_id:
            meeting = await self.db.get_meeting(meeting_id)
            if meeting:
                when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
                text = (
                    f"✅ Редактирование встречи #{meeting_id} завершено.\n\n"
                    f"📌 Тема: {meeting.topic}\n"
                    f"📝 Описание: {meeting.description}\n"
                    f"📅 Дата и время: {when_local:%d.%m.%Y %H:%M}\n"
                    f"👥 Макс. участников: {meeting.max_participants}\n"
                    f"📍 Место: {meeting.location or 'не указано'}"
                )
                if cq:
                    await cq.edit_message_text(text, parse_mode="HTML")
                else:
                    await update.effective_message.reply_text(text, parse_mode="HTML")

        context.user_data.clear()
        return ConversationHandler.END

    async def _edit_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel the edit operation."""
        context.user_data.clear()
        await update.effective_message.reply_text("Редактирование отменено.")
        return ConversationHandler.END

    # ----- Field selection handlers -----

    async def _edit_select_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit the topic."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()
        await cq.edit_message_text("Введи новую тему встречи:")
        return self.STATE_EDIT_TOPIC

    async def _edit_select_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit the description."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()
        await cq.edit_message_text("Введи новое описание встречи:")
        return self.STATE_EDIT_DESCRIPTION

    async def _edit_select_max(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit max participants."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()

        meeting_id = context.user_data.get("edit_meeting_id")
        confirmed = await self.db.count_confirmed(meeting_id) if meeting_id else 0
        await cq.edit_message_text(
            f"Введи новое максимальное количество участников.\n"
            f"(Сейчас зарегистрировано: {confirmed})"
        )
        return self.STATE_EDIT_MAX

    async def _edit_select_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit the location."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()
        await cq.edit_message_text(
            "Введи новое место проведения встречи:\n"
            "(Отправь '-' чтобы убрать место)"
        )
        return self.STATE_EDIT_LOCATION

    async def _edit_select_datetime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit date/time - show month picker."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()
        await cq.edit_message_text(
            "📅 Выбери новый месяц встречи:",
            reply_markup=MonthPickerKeyboard.build()
        )
        return self.STATE_EDIT_MONTH

    # ----- Field value handlers -----

    async def _edit_topic_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle new topic input."""
        topic = (update.effective_message.text or "").strip()
        if not topic:
            await update.effective_message.reply_text("Тема не может быть пустой. Введи новую тему:")
            return self.STATE_EDIT_TOPIC

        meeting_id = context.user_data.get("edit_meeting_id")
        if not meeting_id:
            await update.effective_message.reply_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        meeting = await self.db.update_meeting(meeting_id, topic=topic)
        if not meeting:
            await update.effective_message.reply_text("Встреча не найдена.")
            return ConversationHandler.END

        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        text = (
            f"✅ Тема обновлена!\n\n"
            f"✏️ <b>Редактирование встречи #{meeting_id}</b>\n\n"
            f"📌 Тема: {meeting.topic}\n"
            f"📝 Описание: {meeting.description}\n"
            f"📅 Дата и время: {when_local:%d.%m.%Y %H:%M}\n"
            f"👥 Макс. участников: {meeting.max_participants}\n"
            f"📍 Место: {meeting.location or 'не указано'}\n\n"
            "Выбери, что хочешь изменить:"
        )
        await update.effective_message.reply_text(
            text,
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML"
        )
        return self.STATE_EDIT_MENU

    async def _edit_description_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle new description input."""
        description = (update.effective_message.text or "").strip()
        if not description:
            await update.effective_message.reply_text("Описание не может быть пустым. Введи новое описание:")
            return self.STATE_EDIT_DESCRIPTION

        meeting_id = context.user_data.get("edit_meeting_id")
        if not meeting_id:
            await update.effective_message.reply_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        meeting = await self.db.update_meeting(meeting_id, description=description)
        if not meeting:
            await update.effective_message.reply_text("Встреча не найдена.")
            return ConversationHandler.END

        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        text = (
            f"✅ Описание обновлено!\n\n"
            f"✏️ <b>Редактирование встречи #{meeting_id}</b>\n\n"
            f"📌 Тема: {meeting.topic}\n"
            f"📝 Описание: {meeting.description}\n"
            f"📅 Дата и время: {when_local:%d.%m.%Y %H:%M}\n"
            f"👥 Макс. участников: {meeting.max_participants}\n"
            f"📍 Место: {meeting.location or 'не указано'}\n\n"
            "Выбери, что хочешь изменить:"
        )
        await update.effective_message.reply_text(
            text,
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML"
        )
        return self.STATE_EDIT_MENU

    async def _edit_max_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle new max participants input."""
        text = (update.effective_message.text or "").strip()
        meeting_id = context.user_data.get("edit_meeting_id")

        if not meeting_id:
            await update.effective_message.reply_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        try:
            max_p = int(text)
            if max_p <= 0:
                raise ValueError
        except Exception:
            await update.effective_message.reply_text(
                "Не удалось распознать число. Введи положительное число:"
            )
            return self.STATE_EDIT_MAX

        confirmed = await self.db.count_confirmed(meeting_id)
        if max_p < confirmed:
            await update.effective_message.reply_text(
                f"Нельзя установить лимит меньше текущего числа участников ({confirmed}). "
                f"Введи число >= {confirmed}:"
            )
            return self.STATE_EDIT_MAX

        meeting = await self.db.update_meeting(meeting_id, max_participants=max_p)
        if not meeting:
            await update.effective_message.reply_text("Встреча не найдена.")
            return ConversationHandler.END

        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        text = (
            f"✅ Максимум участников обновлён!\n\n"
            f"✏️ <b>Редактирование встречи #{meeting_id}</b>\n\n"
            f"📌 Тема: {meeting.topic}\n"
            f"📝 Описание: {meeting.description}\n"
            f"📅 Дата и время: {when_local:%d.%m.%Y %H:%M}\n"
            f"👥 Макс. участников: {meeting.max_participants}\n"
            f"📍 Место: {meeting.location or 'не указано'}\n\n"
            "Выбери, что хочешь изменить:"
        )
        await update.effective_message.reply_text(
            text,
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML"
        )
        return self.STATE_EDIT_MENU

    async def _edit_location_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle new location input."""
        raw = (update.effective_message.text or "").strip()
        meeting_id = context.user_data.get("edit_meeting_id")

        if not meeting_id:
            await update.effective_message.reply_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        skip_values = {"-", "—", "пропустить", "нет", "убрать"}
        location = None if raw.lower() in skip_values else raw

        meeting = await self.db.update_meeting(meeting_id, location=location)
        if not meeting:
            await update.effective_message.reply_text("Встреча не найдена.")
            return ConversationHandler.END

        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        text = (
            f"✅ Место обновлено!\n\n"
            f"✏️ <b>Редактирование встречи #{meeting_id}</b>\n\n"
            f"📌 Тема: {meeting.topic}\n"
            f"📝 Описание: {meeting.description}\n"
            f"📅 Дата и время: {when_local:%d.%m.%Y %H:%M}\n"
            f"👥 Макс. участников: {meeting.max_participants}\n"
            f"📍 Место: {meeting.location or 'не указано'}\n\n"
            "Выбери, что хочешь изменить:"
        )
        await update.effective_message.reply_text(
            text,
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML"
        )
        return self.STATE_EDIT_MENU

    # ----- Date/time edit handlers -----

    async def _edit_month_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle month selection for date edit."""
        query = update.callback_query
        if not query:
            return self.STATE_EDIT_MONTH
        await query.answer()

        parsed = MonthPickerKeyboard.parse_callback(query.data)
        if not parsed:
            return self.STATE_EDIT_MONTH

        year, month = parsed
        context.user_data["edit_selected_year"] = year
        context.user_data["edit_selected_month"] = month

        tomorrow = date.today() + timedelta(days=1)
        first_of_month = date(year, month, 1)
        min_date_val = max(first_of_month, tomorrow)
        if month == 12:
            max_date_val = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            max_date_val = date(year, month + 1, 1) - timedelta(days=1)

        calendar, step = MeetingCalendar(
            calendar_id=2,
            current_date=min_date_val,
            min_date=min_date_val,
            max_date=max_date_val
        ).build()
        await query.edit_message_text(
            "📅 Выбери новый день встречи:",
            reply_markup=calendar
        )
        return self.STATE_EDIT_DATE

    async def _edit_calendar_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle calendar day selection for date edit."""
        query = update.callback_query
        if not query:
            return self.STATE_EDIT_DATE
        await query.answer()

        year = context.user_data.get("edit_selected_year")
        month = context.user_data.get("edit_selected_month")
        tomorrow = date.today() + timedelta(days=1)
        first_of_month = date(year, month, 1)
        min_date_val = max(first_of_month, tomorrow)
        if month == 12:
            max_date_val = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            max_date_val = date(year, month + 1, 1) - timedelta(days=1)

        result, key, step = MeetingCalendar(
            calendar_id=2,
            min_date=min_date_val,
            max_date=max_date_val
        ).process(query.data)

        if not result and key:
            await query.edit_message_text(
                "📅 Выбери новый день встречи:",
                reply_markup=key
            )
            return self.STATE_EDIT_DATE

        if result:
            context.user_data["edit_selected_date"] = result
            await query.edit_message_text(
                f"📅 Дата: {result:%d.%m.%Y}\n\n🕐 Выбери час начала встречи:",
                reply_markup=TimePickerKeyboard.build_hours()
            )
            return self.STATE_EDIT_HOUR

        return self.STATE_EDIT_DATE

    async def _edit_hour_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle hour selection for time edit."""
        query = update.callback_query
        if not query:
            return self.STATE_EDIT_HOUR
        await query.answer()

        data = query.data or ""
        try:
            _, hour_str = data.split(":", 1)
            if hour_str == "back":
                await query.edit_message_text(
                    f"📅 Дата: {context.user_data.get('edit_selected_date'):%d.%m.%Y}\n\n"
                    "🕐 Выбери час начала встречи:",
                    reply_markup=TimePickerKeyboard.build_hours()
                )
                return self.STATE_EDIT_HOUR
            hour = int(hour_str)
        except Exception:
            await query.edit_message_text("Ошибка при выборе часа. Попробуй ещё раз.")
            return self.STATE_EDIT_HOUR

        context.user_data["edit_selected_hour"] = hour
        selected_date = context.user_data.get("edit_selected_date")
        await query.edit_message_text(
            f"📅 Дата: {selected_date:%d.%m.%Y}\n"
            f"🕐 Час: {hour:02d}:00\n\n"
            "⏱ Выбери минуты:",
            reply_markup=TimePickerKeyboard.build_minutes(hour)
        )
        return self.STATE_EDIT_MINUTE

    async def _edit_minute_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle minute selection and update the meeting datetime."""
        query = update.callback_query
        if not query:
            return self.STATE_EDIT_MINUTE
        await query.answer()

        data = query.data or ""

        if data == "hour:back":
            selected_date = context.user_data.get("edit_selected_date")
            await query.edit_message_text(
                f"📅 Дата: {selected_date:%d.%m.%Y}\n\n🕐 Выбери час начала встречи:",
                reply_markup=TimePickerKeyboard.build_hours()
            )
            return self.STATE_EDIT_HOUR

        try:
            _, hour_str, minute_str = data.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
        except Exception:
            await query.edit_message_text("Ошибка при выборе времени. Попробуй ещё раз.")
            return self.STATE_EDIT_MINUTE

        selected_date = context.user_data.get("edit_selected_date")
        if not selected_date:
            await query.edit_message_text("Ошибка: дата не выбрана. Начни редактирование заново.")
            return ConversationHandler.END

        meeting_id = context.user_data.get("edit_meeting_id")
        if not meeting_id:
            await query.edit_message_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        local_dt = datetime(
            year=selected_date.year,
            month=selected_date.month,
            day=selected_date.day,
            hour=hour,
            minute=minute,
            tzinfo=self.local_tz
        )
        start_utc = local_dt.astimezone(tz.UTC)

        meeting = await self.db.update_meeting(meeting_id, start_at_utc=start_utc)
        if not meeting:
            await query.edit_message_text("Встреча не найдена.")
            return ConversationHandler.END

        self.scheduler.schedule_meeting_reminders(meeting)

        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        text = (
            f"✅ Дата и время обновлены!\n\n"
            f"✏️ <b>Редактирование встречи #{meeting_id}</b>\n\n"
            f"📌 Тема: {meeting.topic}\n"
            f"📝 Описание: {meeting.description}\n"
            f"📅 Дата и время: {when_local:%d.%m.%Y %H:%M}\n"
            f"👥 Макс. участников: {meeting.max_participants}\n"
            f"📍 Место: {meeting.location or 'не указано'}\n\n"
            "Выбери, что хочешь изменить:"
        )
        await query.edit_message_text(
            text,
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML"
        )
        return self.STATE_EDIT_MENU

    # ========== Create Meeting Conversation Handlers ==========

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
            "Принято! Описание получено.\nПожалуйста, укажи максимальное количество участников для этой встречи, не включая ведущих.\nПример: 5"
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

    async def _create_meeting_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = (update.effective_message.text or "").strip()
        skip_values = {"", "-", "—", "пропустить", "нет"}
        location = None if raw.lower() in skip_values else raw
        context.user_data["location"] = location
        received = "не указано" if not location else f"{location}"
        # Show month picker for date selection
        await update.effective_message.reply_text(
            f"Принято! Место проведения: {received}.\n\n"
            f"📅 Выбери месяц встречи:",
            reply_markup=MonthPickerKeyboard.build()
        )
        return self.STATE_MONTH

    async def _create_meeting_month_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle month button presses for month selection."""
        query = update.callback_query
        if not query:
            return self.STATE_MONTH
        await query.answer()

        parsed = MonthPickerKeyboard.parse_callback(query.data)
        if not parsed:
            return self.STATE_MONTH

        year, month = parsed
        context.user_data["selected_year"] = year
        context.user_data["selected_month"] = month

        # Calculate min and max dates for the calendar (only selected month)
        tomorrow = date.today() + timedelta(days=1)
        first_of_month = date(year, month, 1)
        # min_date: first of selected month, but not before tomorrow
        min_date_val = max(first_of_month, tomorrow)
        # max_date: last day of selected month
        if month == 12:
            max_date_val = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            max_date_val = date(year, month + 1, 1) - timedelta(days=1)

        # Show day calendar for the selected month
        calendar, step = MeetingCalendar(
            calendar_id=1,
            current_date=min_date_val,
            min_date=min_date_val,
            max_date=max_date_val
        ).build()
        await query.edit_message_text(
            f"📅 Выбери день встречи:",
            reply_markup=calendar
        )
        return self.STATE_DATE

    async def _create_meeting_calendar_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle calendar button presses for date selection."""
        query = update.callback_query
        if not query:
            return self.STATE_DATE
        await query.answer()

        # Reconstruct date constraints for the selected month
        year = context.user_data.get("selected_year")
        month = context.user_data.get("selected_month")
        tomorrow = date.today() + timedelta(days=1)
        first_of_month = date(year, month, 1)
        min_date_val = max(first_of_month, tomorrow)
        if month == 12:
            max_date_val = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            max_date_val = date(year, month + 1, 1) - timedelta(days=1)

        result, key, step = MeetingCalendar(
            calendar_id=1,
            min_date=min_date_val,
            max_date=max_date_val
        ).process(query.data)

        if not result and key:
            # User is still navigating the calendar
            await query.edit_message_text(
                f"📅 Выбери день встречи:",
                reply_markup=key
            )
            return self.STATE_DATE

        if result:
            # Date selected, store it and show hour picker
            context.user_data["selected_date"] = result
            await query.edit_message_text(
                f"📅 Дата: {result:%d.%m.%Y}\n\n🕐 Выбери час начала встречи:",
                reply_markup=TimePickerKeyboard.build_hours()
            )
            return self.STATE_HOUR

        return self.STATE_DATE

    async def _create_meeting_hour_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle hour button presses for time selection."""
        query = update.callback_query
        if not query:
            return self.STATE_HOUR
        await query.answer()

        data = query.data or ""
        try:
            _, hour_str = data.split(":", 1)
            if hour_str == "back":
                # User wants to go back to hour selection - already here, just refresh
                await query.edit_message_text(
                    f"📅 Дата: {context.user_data.get('selected_date'):%d.%m.%Y}\n\n"
                    "🕐 Выбери час начала встречи:",
                    reply_markup=TimePickerKeyboard.build_hours()
                )
                return self.STATE_HOUR
            hour = int(hour_str)
        except Exception:
            await query.edit_message_text("Ошибка при выборе часа. Попробуй ещё раз.")
            return self.STATE_HOUR

        # Store selected hour and show minute picker
        context.user_data["selected_hour"] = hour
        selected_date = context.user_data.get("selected_date")
        await query.edit_message_text(
            f"📅 Дата: {selected_date:%d.%m.%Y}\n"
            f"🕐 Час: {hour:02d}:00\n\n"
            "⏱ Выбери минуты:",
            reply_markup=TimePickerKeyboard.build_minutes(hour)
        )
        return self.STATE_MINUTE

    async def _create_meeting_minute_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle minute button presses and create the meeting."""
        query = update.callback_query
        if not query:
            return self.STATE_MINUTE
        await query.answer()

        data = query.data or ""

        # Check if user wants to go back to hour selection
        if data == "hour:back":
            selected_date = context.user_data.get("selected_date")
            await query.edit_message_text(
                f"📅 Дата: {selected_date:%d.%m.%Y}\n\n🕐 Выбери час начала встречи:",
                reply_markup=TimePickerKeyboard.build_hours()
            )
            return self.STATE_HOUR

        # Extract hour and minute from callback data (format: "time:HH:MM")
        try:
            _, hour_str, minute_str = data.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
        except Exception:
            await query.edit_message_text("Ошибка при выборе времени. Попробуй ещё раз.")
            return self.STATE_MINUTE

        # Combine date and time
        selected_date = context.user_data.get("selected_date")
        if not selected_date:
            await query.edit_message_text("Ошибка: дата не выбрана. Начни заново с /create_meeting")
            return ConversationHandler.END

        # Create datetime in local timezone and convert to UTC
        local_dt = datetime(
            year=selected_date.year,
            month=selected_date.month,
            day=selected_date.day,
            hour=hour,
            minute=minute,
            tzinfo=self.local_tz
        )
        start_utc = local_dt.astimezone(tz.UTC)

        # Collect all data and create meeting
        user = update.effective_user
        if not user:
            await query.edit_message_text("Произошла ошибка. Попробуй ещё раз позже.")
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
        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        summary = (
            "✅ Спасибо! Встреча создана.\n\n"
            f"📌 Название: {topic}\n"
            f"📝 Описание: {description}\n"
            f"📅 Дата и время (Берлин): {when_local:%d.%m.%Y %H:%M}\n"
            f"📍 Место: {location or 'не указано'}\n"
            f"👥 Максимум участников: {max_participants}\n"
            f"🆔 ID встречи: #{meeting.id}"
        )
        await query.edit_message_text(summary)
        context.user_data.clear()
        return ConversationHandler.END

    async def _create_meeting_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.effective_message.reply_text("Создание встречи отменено.")
        return ConversationHandler.END

    # ========== Command Handlers ==========

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
        await update.effective_message.reply_text(
            msg,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    async def cmd_meetings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List all upcoming meetings with register/details buttons."""
        user = update.effective_user
        now_utc = datetime.now(tz.UTC)
        meetings = await self.db.list_upcoming_meetings(now_utc)
        if not meetings:
            await update.effective_message.reply_text("No upcoming meetings.")
            return

        for m in meetings:
            when_local = ensure_utc(m.start_at_utc).astimezone(self.local_tz)
            host_name = await self.db.get_user_name(m.created_by) or "Unknown"
            confirmed = await self.db.count_confirmed(m.id)
            hosts = await self.db.count_hosts(m.id)
            available = max(m.max_participants - confirmed, 0)
            text = (
                f"<b>{when_local:%Y-%m-%d %H:%M}</b>\n"
                f"<b>{m.topic}</b>\n"
                f"Ведет: {host_name}\n"
                f"Свободных мест: {available} (+ведущих: {hosts})"
            )
            if user and m.created_by == user.id:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(text="Подробности", callback_data=f"details:{m.id}"),
                    InlineKeyboardButton(text="Изменить", callback_data=f"edit:{m.id}"),
                    InlineKeyboardButton(text="Отменить", callback_data=f"cancel:{m.id}")
                ]])
            else:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(text="Записаться", callback_data=f"register:{m.id}"),
                    InlineKeyboardButton(text="Подробности", callback_data=f"details:{m.id}")
                ]])
            await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

    async def cmd_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List upcoming meetings the user is registered for."""
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        now_utc = datetime.now(tz.UTC)
        meetings = await self.db.list_user_meetings(user.id, now_utc)
        if not meetings:
            await update.effective_message.reply_text("You have no meetings yet.")
            return

        for m in meetings:
            when_local = ensure_utc(m.start_at_utc).astimezone(self.local_tz)
            host_name = await self.db.get_user_name(m.created_by) or "Unknown"
            confirmed = await self.db.count_confirmed(m.id)
            hosts = await self.db.count_hosts(m.id)
            available = max(m.max_participants - confirmed, 0)
            text = (
                f"<b>{when_local:%Y-%m-%d %H:%M}</b>\n"
                f"<b>{m.topic}</b>\n"
                f"Ведет: {host_name}\n"
                f"Свободных мест: {available} (+ведущих: {hosts})"
            )
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(text="Подробности", callback_data=f"details:{m.id}"),
                InlineKeyboardButton(text="Изменить", callback_data=f"edit:{m.id}"),
                InlineKeyboardButton(text="Отменить", callback_data=f"cancel:{m.id}")
            ]])
            await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

    async def cmd_register(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Register current user for a meeting by ID."""
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
        """Unregister current user from a meeting by ID."""
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

    # ========== Callback Query Handlers ==========

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

        when_local = ensure_utc(m.start_at_utc).astimezone(self.local_tz)
        date_str = when_local.strftime("%A, %d %B %Y")
        time_str = when_local.strftime("%H:%M")

        host_user = await self.db.get_user(m.created_by)
        if host_user:
            host_display = host_user.name or (host_user.username or "Unknown")
            if host_user.username:
                host_display = (
                    f"{host_user.name} (@{host_user.username})"
                    if host_user.name
                    else f"@{host_user.username}"
                )
        else:
            host_display = "Unknown"

        confirmed = await self.db.count_confirmed(m.id)
        hosts = await self.db.count_hosts(m.id)
        details_text = (
            f"<b>{m.topic}</b>\n"
            f"📝  {m.description}\n\n\n"
            f"📅 Date: {date_str}\n"
            f"🕐 Time: {time_str} (Berlin time)\n"
            f"📍 Location {m.location or 'TBA'}\n"
            f"👤 Ведет: {host_display}\n"
            f"👥 Идет: {confirmed} / {m.max_participants} participants (+ведущих: {hosts})"
        )
        await cq.message.reply_text(details_text, parse_mode="HTML")

    async def cb_cancel_meeting(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline 'Отменить' button presses - show confirmation."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()

        user = update.effective_user
        if not user:
            return

        data = cq.data or ""
        try:
            _, meeting_id_str = data.split(":", 1)
            meeting_id = int(meeting_id_str)
        except Exception:
            await cq.message.reply_text("Ошибка: неверный запрос.")
            return

        meeting = await self.db.get_meeting(meeting_id)
        if not meeting:
            await cq.message.reply_text("Встреча не найдена.")
            return

        if meeting.created_by != user.id:
            await cq.message.reply_text("Только автор встречи может её отменить.")
            return

        if meeting.canceled_at:
            await cq.message.reply_text("Эта встреча уже отменена.")
            return

        when_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        confirmed = await self.db.count_confirmed(meeting.id)

        confirmation_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"cancel_confirm:{meeting_id}"),
                InlineKeyboardButton(text="❌ Нет, оставить", callback_data=f"cancel_abort:{meeting_id}"),
            ]
        ])

        text = (
            f"⚠️ <b>Отмена встречи #{meeting_id}</b>\n\n"
            f"📌 {meeting.topic}\n"
            f"📅 {when_local:%d.%m.%Y %H:%M}\n"
            f"👥 Зарегистрировано участников: {confirmed}\n\n"
            "Ты уверен(а), что хочешь отменить эту встречу?\n"
            "Это действие нельзя отменить."
        )
        await cq.message.reply_text(text, reply_markup=confirmation_keyboard, parse_mode="HTML")

    async def cb_cancel_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle confirmation of meeting cancellation."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()

        user = update.effective_user
        if not user:
            return

        data = cq.data or ""
        try:
            _, meeting_id_str = data.split(":", 1)
            meeting_id = int(meeting_id_str)
        except Exception:
            await cq.message.reply_text("Ошибка: неверный запрос.")
            return

        meeting = await self.db.get_meeting(meeting_id)
        if not meeting:
            await cq.message.reply_text("Встреча не найдена.")
            return

        if meeting.created_by != user.id:
            await cq.message.reply_text("Только автор встречи может её отменить.")
            return

        if meeting.canceled_at:
            await cq.edit_message_text("Эта встреча уже отменена.")
            return

        canceled_meeting = await self.db.cancel_meeting(meeting_id, datetime.now(tz.UTC))
        if not canceled_meeting:
            await cq.edit_message_text("Ошибка при отмене встречи.")
            return

        when_local = ensure_utc(canceled_meeting.start_at_utc).astimezone(self.local_tz)
        text = (
            f"🚫 <b>Встреча отменена</b>\n\n"
            f"📌 {canceled_meeting.topic}\n"
            f"📅 {when_local:%d.%m.%Y %H:%M}\n\n"
            "Встреча успешно отменена."
        )
        await cq.edit_message_text(text, parse_mode="HTML")

    async def cb_cancel_abort(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle abort of meeting cancellation."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()

        data = cq.data or ""
        try:
            _, meeting_id_str = data.split(":", 1)
            meeting_id = int(meeting_id_str)
        except Exception:
            await cq.message.reply_text("Ошибка: неверный запрос.")
            return

        await cq.edit_message_text(f"Отмена встречи #{meeting_id} отклонена. Встреча остаётся активной.")
