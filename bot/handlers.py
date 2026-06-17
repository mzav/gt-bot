"""Telegram command handlers and application builder for the bot."""
from __future__ import annotations

import contextlib
import logging
import re
from datetime import datetime, date, timedelta
from typing import Literal

from dateutil import tz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
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

from .access_control import has_community_access, is_channel_member
from .config import Settings
from .google_calendar import (
    build_calendar_offer,
    can_offer_google_calendar,
    gcal_disclaimer,
    gcal_update_reminder,
    google_calendar_keyboard,
    build_google_calendar_event_url,
)
from .links import parse_start_payload
from .meeting_actions import resolve_meeting_actions
from .meeting_format import format_meeting_time, format_registration_start
from .meeting_notifications import (
    detect_important_changes,
    notify_participants,
    snapshot_meeting,
)
from .messages import RESTRICTED_ACCESS_MESSAGE, WELCOME_MESSAGE
from .models import Meeting, User, RegistrationStatus, WaitlistStatus
from .storage import Database, is_registration_open
from .scheduler import BotScheduler
from .waitlist import WaitlistService, OfferNotification, send_offer_dms
from .utils import ensure_utc, message_text_as_html
from .keyboards import (
    MeetingCalendar,
    MonthPickerKeyboard,
    TimePickerKeyboard,
    LSTEP_TRANSLATIONS,
    photo_skip_keyboard,
    edit_photo_keyboard,
    append_cancel_row,
    CONV_CANCEL_CALLBACK,
)
from .main_menu import (
    MENU_CREATE,
    MENU_FORCE_SUMMARY,
    MENU_HELP,
    MENU_MEETINGS,
    build_main_menu_keyboard,
    MENU_MY,
    menu_label_filter,
    remove_main_menu_keyboard,
)
from .registration_confirmation import (
    build_cancelled_keyboard,
    build_full_keyboard,
    build_offer_confirm_keyboard,
    build_offer_overlap_confirm_keyboard,
    build_overlap_confirm_keyboard,
    build_overlap_decline_keyboard,
    build_step1_keyboard,
    build_step2_keyboard,
    build_step3_keyboard,
    build_unavailable_keyboard,
    format_cancelled,
    format_full,
    format_offer_short_confirm,
    format_overlap_confirm,
    format_overlap_declined,
    format_overlapping_meetings_summary,
    format_step1,
    format_step2,
    format_step3,
    format_unavailable,
    preflight_registration,
)
from .meeting_overlap import find_overlapping_meetings
from .cancellation_confirmation import (
    CancellationReasonType,
    LEAVE_OTHER_PENDING_KEY,
    LEAVE_OTHER_TEXT_KEY,
    MAX_OTHER_REASON_LEN,
    build_final_confirm_keyboard,
    build_other_reason_keyboard,
    build_reason_keyboard,
    build_stayed_keyboard,
    format_final_confirm,
    format_other_reason_prompt,
    format_reason_prompt,
    format_stayed_registered,
    parse_leave_confirm_callback,
    parse_leave_reason_callback,
    preflight_leave,
)

_CAPTION_LIMIT = 1024
_CREATE_CANCEL_MESSAGE = "Создание встречи отменено."
_EDIT_CANCEL_MESSAGE = "Редактирование отменено."
_CONVERSATION_ESCAPE_HINT = "\n\nВ любой момент можно отменить: /cancel или кнопка «Отмена»."


async def _reply_with_card(
    message,
    text: str,
    photo_file_id: str | None,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Reply with photo+caption when available, falling back to text-only."""
    if photo_file_id:
        if len(text) <= _CAPTION_LIMIT:
            await message.reply_photo(
                photo_file_id,
                caption=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        else:
            await message.reply_photo(photo_file_id)
            await message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

logger = logging.getLogger(__name__)


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
    STATE_PHOTO = 9
    STATE_END_HOUR = 20
    STATE_END_MINUTE = 21
    STATE_REG_START_CHOICE = 22

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
    STATE_EDIT_PHOTO = 19
    STATE_EDIT_END_HOUR = 23
    STATE_EDIT_END_MINUTE = 24
    STATE_EDIT_REG_START_CHOICE = 25

    def __init__(self, settings: Settings, db: Database, scheduler: BotScheduler, waitlist: WaitlistService):
        self.settings = settings
        self.db = db
        self.scheduler = scheduler
        self.waitlist = waitlist
        self.local_tz = tz.gettz(settings.tz)
        self.bot_username: str | None = settings.telegram_bot_username

    def build(self) -> Application:
        """Create and configure the PTB Application with command handlers."""
        app = ApplicationBuilder().token(self.settings.telegram_bot_token).build()
        G = self._community_gated

        app.add_handler(CommandHandler("force_summary", self.cmd_force_summary))
        app.add_handler(CommandHandler(["start", "help"], self.cmd_start))
        app.add_handler(CommandHandler("upcoming_meetings", G(self.cmd_meetings)))
        app.add_handler(CommandHandler("my_meetings", G(self.cmd_my)))
        app.add_handler(self._build_create_meeting_handler())
        app.add_handler(CommandHandler("register", G(self.cmd_register)))
        app.add_handler(CommandHandler("unregister", G(self.cmd_unregister)))
        app.add_handler(CallbackQueryHandler(G(self.cb_register), pattern=r"^register:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_reg_s1_yes), pattern=r"^reg_s1_yes:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_reg_s1_no), pattern=r"^reg_s1_no:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_reg_s2_yes), pattern=r"^reg_s2_yes:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_reg_s2_no), pattern=r"^reg_s2_no:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_reg_s3_yes), pattern=r"^reg_s3_yes:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_reg_s3_no), pattern=r"^reg_s3_no:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_reg_overlap_yes), pattern=r"^reg_overlap_yes:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_reg_overlap_no), pattern=r"^reg_overlap_no:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_details), pattern=r"^details:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_participants), pattern=r"^participants:\d+$"))
        app.add_handler(self._build_edit_meeting_handler())
        app.add_handler(CallbackQueryHandler(G(self.cb_cancel_meeting), pattern=r"^cancel:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_cancel_confirm), pattern=r"^cancel_confirm:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_cancel_abort), pattern=r"^cancel_abort:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_leave_meeting), pattern=r"^leave:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_leave_reason), pattern=r"^leave_r:(ill|family|other):\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_leave_other_abort), pattern=r"^leave_other_abort:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_leave_confirm), pattern=r"^leave_confirm:(ill|family|other):\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_leave_abort), pattern=r"^leave_abort:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_show_upcoming), pattern=r"^show_upcoming$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_waitlist_join), pattern=r"^waitlist_join:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_waitlist_cancel), pattern=r"^waitlist_cancel:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_waitlist_view), pattern=r"^waitlist:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_offer_accept), pattern=r"^offer_accept:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_offer_confirm), pattern=r"^offer_confirm:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_offer_decline), pattern=r"^offer_decline:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_offer_overlap_yes), pattern=r"^offer_overlap_yes:\d+$"))
        app.add_handler(CallbackQueryHandler(G(self.cb_offer_overlap_no), pattern=r"^offer_overlap_no:\d+$"))
        app.add_handler(MessageHandler(menu_label_filter(), self.handle_main_menu_text))
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_unknown_text),
        )

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
        GC = lambda h: self._community_gated(h, conv=True)
        return ConversationHandler(
            entry_points=[
                CommandHandler("create_meeting", GC(self._create_meeting_start)),
                MessageHandler(
                    filters.Regex(f"^{re.escape(MENU_CREATE)}$"),
                    GC(self._create_meeting_start),
                ),
            ],
            states={
                self.STATE_TOPIC: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._create_meeting_topic))
                ],
                self.STATE_DESCRIPTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._create_meeting_description))
                ],
                self.STATE_MAX: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._create_meeting_max_members))
                ],
                self.STATE_LOCATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._create_meeting_location))
                ],
                self.STATE_MONTH: [
                    CallbackQueryHandler(GC(self._create_meeting_month_callback), pattern=r"^month:")
                ],
                self.STATE_DATE: [
                    CallbackQueryHandler(GC(self._create_meeting_calendar_callback), pattern=r"^cbcal_")
                ],
                self.STATE_HOUR: [
                    CallbackQueryHandler(GC(self._create_meeting_hour_callback), pattern=r"^hour:")
                ],
                self.STATE_MINUTE: [
                    CallbackQueryHandler(GC(self._create_meeting_minute_callback), pattern=r"^time:"),
                    CallbackQueryHandler(GC(self._create_meeting_minute_callback), pattern=r"^hour:back$"),
                ],
                self.STATE_END_HOUR: [
                    CallbackQueryHandler(GC(self._create_meeting_end_hour_callback), pattern=r"^hour:"),
                ],
                self.STATE_END_MINUTE: [
                    CallbackQueryHandler(GC(self._create_meeting_end_minute_callback), pattern=r"^time:"),
                    CallbackQueryHandler(GC(self._create_meeting_end_minute_callback), pattern=r"^hour:back$"),
                ],
                self.STATE_REG_START_CHOICE: [
                    CallbackQueryHandler(GC(self._create_reg_start_choice), pattern=r"^reg_start:"),
                ],
                self.STATE_PHOTO: [
                    MessageHandler(filters.PHOTO, GC(self._create_meeting_photo)),
                    CallbackQueryHandler(GC(self._create_meeting_photo), pattern=r"^skip_photo$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._create_meeting_photo_invalid)),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", GC(self._create_meeting_cancel)),
                CommandHandler(["start", "help"], GC(self._create_meeting_start_fallback)),
                MessageHandler(menu_label_filter(), GC(self._create_meeting_menu_fallback)),
                CallbackQueryHandler(
                    GC(self._create_meeting_cancel_callback),
                    pattern=f"^{re.escape(CONV_CANCEL_CALLBACK)}$",
                ),
            ],
            allow_reentry=True,
        )

    def _build_edit_meeting_handler(self) -> ConversationHandler:
        """Build the conversation handler for editing a meeting."""
        GC = lambda h: self._community_gated(h, conv=True)
        return ConversationHandler(
            entry_points=[
                CallbackQueryHandler(GC(self._edit_meeting_start), pattern=r"^edit:\d+$"),
                CallbackQueryHandler(GC(self._edit_done), pattern=r"^edit_field:done$"),
            ],
            states={
                self.STATE_EDIT_MENU: [
                    CallbackQueryHandler(GC(self._edit_select_topic), pattern=r"^edit_field:topic$"),
                    CallbackQueryHandler(GC(self._edit_select_description), pattern=r"^edit_field:description$"),
                    CallbackQueryHandler(GC(self._edit_select_max), pattern=r"^edit_field:max$"),
                    CallbackQueryHandler(GC(self._edit_select_location), pattern=r"^edit_field:location$"),
                    CallbackQueryHandler(GC(self._edit_select_datetime), pattern=r"^edit_field:datetime$"),
                    CallbackQueryHandler(GC(self._edit_select_endtime), pattern=r"^edit_field:endtime$"),
                    CallbackQueryHandler(GC(self._edit_select_regstart), pattern=r"^edit_field:regstart$"),
                    CallbackQueryHandler(GC(self._edit_select_photo), pattern=r"^edit_field:photo$"),
                    CallbackQueryHandler(GC(self._edit_reg_start_choice), pattern=r"^edit_reg_start:"),
                    CallbackQueryHandler(GC(self._edit_done), pattern=r"^edit_field:done$"),
                ],
                self.STATE_EDIT_TOPIC: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._edit_topic_handler))
                ],
                self.STATE_EDIT_DESCRIPTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._edit_description_handler))
                ],
                self.STATE_EDIT_MAX: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._edit_max_handler))
                ],
                self.STATE_EDIT_LOCATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._edit_location_handler))
                ],
                self.STATE_EDIT_MONTH: [
                    CallbackQueryHandler(GC(self._edit_month_callback), pattern=r"^month:")
                ],
                self.STATE_EDIT_DATE: [
                    CallbackQueryHandler(GC(self._edit_calendar_callback), pattern=r"^cbcal_")
                ],
                self.STATE_EDIT_HOUR: [
                    CallbackQueryHandler(GC(self._edit_hour_callback), pattern=r"^hour:")
                ],
                self.STATE_EDIT_MINUTE: [
                    CallbackQueryHandler(GC(self._edit_minute_callback), pattern=r"^time:"),
                    CallbackQueryHandler(GC(self._edit_minute_callback), pattern=r"^hour:back$"),
                ],
                self.STATE_EDIT_END_HOUR: [
                    CallbackQueryHandler(GC(self._edit_end_hour_callback), pattern=r"^hour:"),
                ],
                self.STATE_EDIT_END_MINUTE: [
                    CallbackQueryHandler(GC(self._edit_end_minute_callback), pattern=r"^time:"),
                    CallbackQueryHandler(GC(self._edit_end_minute_callback), pattern=r"^hour:back$"),
                ],
                self.STATE_EDIT_REG_START_CHOICE: [
                    CallbackQueryHandler(GC(self._edit_reg_start_choice), pattern=r"^edit_reg_start:"),
                ],
                self.STATE_EDIT_PHOTO: [
                    MessageHandler(filters.PHOTO, GC(self._edit_photo_handler)),
                    CallbackQueryHandler(GC(self._edit_photo_handler), pattern=r"^edit_photo:"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, GC(self._edit_photo_invalid)),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", GC(self._edit_cancel)),
                CommandHandler(["start", "help"], GC(self._edit_start_fallback)),
                MessageHandler(menu_label_filter(), GC(self._edit_menu_fallback)),
                CallbackQueryHandler(GC(self._edit_done), pattern=r"^edit_field:done$"),
                CallbackQueryHandler(
                    GC(self._edit_cancel_callback),
                    pattern=f"^{re.escape(CONV_CANCEL_CALLBACK)}$",
                ),
            ],
            allow_reentry=True,
            per_message=False,
        )

    def _is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.settings.admin_user_ids

    async def _user_has_community_access(
        self, user_id: int, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        if self._is_admin(user_id):
            return True
        channel_id = self.settings.announcements_channel_id
        if not channel_id:
            logger.warning("Community access denied: announcements channel not configured")
            return False
        is_member = await is_channel_member(context.bot, channel_id, user_id)
        return has_community_access(self.settings, user_id, is_member=is_member)

    async def _send_restricted_access(self, update: Update) -> None:
        cq = update.callback_query
        if cq is not None:
            await cq.answer()
            message = cq.message
        else:
            message = update.effective_message
        if not message:
            return
        await message.reply_text(
            RESTRICTED_ACCESS_MESSAGE,
            reply_markup=remove_main_menu_keyboard(),
        )

    async def _ensure_community_access(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        user = update.effective_user
        if not user:
            return False
        if await self._user_has_community_access(user.id, context):
            return True
        await self._send_restricted_access(update)
        return False

    def _community_gated(self, handler, *, conv: bool = False):
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await self._ensure_community_access(update, context):
                return ConversationHandler.END if conv else None
            return await handler(update, context)
        return wrapped

    def _main_menu_markup(self, user_id: int | None):
        return build_main_menu_keyboard(is_admin=self._is_admin(user_id))

    async def _hide_main_menu(self, message) -> None:
        sent = await message.reply_text(".", reply_markup=remove_main_menu_keyboard())
        with contextlib.suppress(TelegramError):
            await sent.delete()

    async def _restore_main_menu(self, message, user_id: int | None) -> None:
        sent = await message.reply_text(".", reply_markup=self._main_menu_markup(user_id))
        with contextlib.suppress(TelegramError):
            await sent.delete()

    async def _finish_conversation_cancel(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        message: str,
    ) -> int:
        context.user_data.clear()
        user = update.effective_user
        menu = self._main_menu_markup(user.id if user else None)
        cq = update.callback_query
        if cq:
            await cq.answer()
            with contextlib.suppress(TelegramError):
                await cq.edit_message_text(message)
            if cq.message:
                await self._restore_main_menu(cq.message, user.id if user else None)
        else:
            effective = update.effective_message
            if effective:
                await effective.reply_text(message, reply_markup=menu)
        return ConversationHandler.END

    async def _create_meeting_start_fallback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        context.user_data.clear()
        user = update.effective_user
        message = update.effective_message
        if user and message:
            await self.db.get_or_create_user(user.id, user.full_name, user.username)
            await self._send_welcome_message(message, user_id=user.id)
        return ConversationHandler.END

    async def _create_meeting_menu_fallback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        message = update.effective_message
        if not message or not message.text:
            return ConversationHandler.END
        text = message.text.strip()
        if text == MENU_CREATE:
            return await self._create_meeting_start(update, context)
        context.user_data.clear()
        if text == MENU_MEETINGS:
            await self.cmd_meetings(update, context)
        elif text == MENU_MY:
            await self.cmd_my(update, context)
        elif text == MENU_HELP:
            user = update.effective_user
            await self._send_welcome_message(message, user_id=user.id if user else None)
        return ConversationHandler.END

    async def _create_meeting_cancel_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        return await self._finish_conversation_cancel(
            update, context, message=_CREATE_CANCEL_MESSAGE
        )

    async def _edit_start_fallback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        context.user_data.clear()
        user = update.effective_user
        message = update.effective_message
        if user and message:
            await self.db.get_or_create_user(user.id, user.full_name, user.username)
            await self._send_welcome_message(message, user_id=user.id)
        return ConversationHandler.END

    async def _edit_menu_fallback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        message = update.effective_message
        if not message or not message.text:
            return ConversationHandler.END
        text = message.text.strip()
        if text == MENU_CREATE:
            return await self._create_meeting_start(update, context)
        context.user_data.clear()
        if text == MENU_MEETINGS:
            await self.cmd_meetings(update, context)
        elif text == MENU_MY:
            await self.cmd_my(update, context)
        elif text == MENU_HELP:
            user = update.effective_user
            await self._send_welcome_message(message, user_id=user.id if user else None)
        return ConversationHandler.END

    async def _edit_cancel_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        return await self._finish_conversation_cancel(
            update, context, message=_EDIT_CANCEL_MESSAGE
        )

    def _build_meeting_actions_keyboard(
        self,
        meeting_id: int,
        user_id: int | None,
        created_by: int,
        *,
        include_register: bool = False,
        is_participant: bool = False,
        available: int = 1,
        waitlist_state: str | None = None,
        waitlist_count: int = 0,
        registration_open: bool = True,
    ) -> InlineKeyboardMarkup:
        """Build action buttons for a meeting list entry."""
        is_host = user_id is not None and created_by == user_id
        action = resolve_meeting_actions(
            is_host=is_host,
            is_participant=is_participant,
            available=available,
            waitlist_state=waitlist_state,
            include_register=include_register,
            registration_open=registration_open,
        )
        if action == "host":
            buttons = [
                [
                    InlineKeyboardButton(text="Подробности", callback_data=f"details:{meeting_id}"),
                    InlineKeyboardButton(text="Участники", callback_data=f"participants:{meeting_id}"),
                ],
            ]
            if waitlist_count > 0:
                buttons.append([
                    InlineKeyboardButton(text="Waitlist", callback_data=f"waitlist:{meeting_id}"),
                ])
            buttons.append([
                InlineKeyboardButton(text="Изменить", callback_data=f"edit:{meeting_id}"),
                InlineKeyboardButton(text="Отменить", callback_data=f"cancel:{meeting_id}"),
            ])
            return InlineKeyboardMarkup(buttons)
        if action == "register":
            buttons = [
                InlineKeyboardButton(text="Подробности", callback_data=f"details:{meeting_id}"),
                InlineKeyboardButton(text="Записаться", callback_data=f"register:{meeting_id}"),
            ]
        elif action == "participant":
            buttons = [
                InlineKeyboardButton(text="Подробности", callback_data=f"details:{meeting_id}"),
                InlineKeyboardButton(text="Отменить участие", callback_data=f"leave:{meeting_id}"),
            ]
        elif action == "waitlist_join":
            buttons = [
                InlineKeyboardButton(text="Подробности", callback_data=f"details:{meeting_id}"),
                InlineKeyboardButton(text="В waitlist", callback_data=f"waitlist_join:{meeting_id}"),
            ]
        elif action == "waitlist_waiting":
            buttons = [
                InlineKeyboardButton(text="Подробности", callback_data=f"details:{meeting_id}"),
                InlineKeyboardButton(text="Отменить waitlist", callback_data=f"waitlist_cancel:{meeting_id}"),
            ]
        else:
            buttons = [
                InlineKeyboardButton(text="Подробности", callback_data=f"details:{meeting_id}"),
            ]
        return InlineKeyboardMarkup([buttons])

    def _build_waitlist_join_keyboard(self, meeting_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(text="Отменить waitlist", callback_data=f"waitlist_cancel:{meeting_id}"),
                InlineKeyboardButton(text="Показать встречу", callback_data=f"details:{meeting_id}"),
            ],
        ])

    @staticmethod
    def _parse_callback_meeting_id(data: str) -> int | None:
        try:
            return int(data.split(":", 1)[1])
        except (IndexError, ValueError):
            return None

    async def _reply_preflight_error(self, message, meeting_id: int, error: str) -> None:
        if error == format_full():
            await message.reply_text(error, reply_markup=build_full_keyboard(meeting_id))
        elif error == format_unavailable():
            await message.reply_text(error, reply_markup=build_unavailable_keyboard())
        else:
            await message.reply_text(error)

    async def _find_registration_overlaps(self, user_id: int, meeting: Meeting, now_utc) -> list[Meeting]:
        return await find_overlapping_meetings(
            self.db, user_id, meeting, self.local_tz, now_utc
        )

    async def _reply_overlap_confirm(
        self, message, meeting: Meeting, user_id: int, now_utc, *, keyboard_builder
    ) -> bool:
        """Show overlap confirmation when conflicts exist. Returns True if shown."""
        overlaps = await self._find_registration_overlaps(user_id, meeting, now_utc)
        if not overlaps:
            return False
        summary = format_overlapping_meetings_summary(overlaps, self.local_tz)
        await message.reply_text(
            format_overlap_confirm(summary),
            reply_markup=keyboard_builder(),
        )
        return True

    async def _start_registration_flow(self, message, meeting_id: int, user_id: int) -> None:
        result = await preflight_registration(
            self.db, meeting_id, user_id, self.local_tz
        )
        if result.error:
            await self._reply_preflight_error(message, meeting_id, result.error)
            return
        await message.reply_text(
            format_step1(result.meeting, self.local_tz),
            reply_markup=build_step1_keyboard(meeting_id),
            parse_mode="HTML",
        )

    async def _reply_registration_cancelled(self, message, meeting_id: int) -> None:
        await message.reply_text(
            format_cancelled(),
            reply_markup=build_cancelled_keyboard(meeting_id),
        )

    @staticmethod
    def _clear_leave_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data.pop(LEAVE_OTHER_PENDING_KEY, None)
        context.user_data.pop(LEAVE_OTHER_TEXT_KEY, None)

    async def _reply_leave_preflight_error(self, message, error: str) -> None:
        if error == format_unavailable():
            await message.reply_text(error, reply_markup=build_unavailable_keyboard())
        else:
            await message.reply_text(error)

    async def _show_final_leave_confirm(
        self,
        message,
        meeting_id: int,
        reason_type: str,
    ) -> None:
        await message.reply_text(
            format_final_confirm(),
            reply_markup=build_final_confirm_keyboard(meeting_id, reason_type),
        )

    async def _complete_leave(
        self,
        message,
        meeting_id: int,
        user_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        reason_type: str,
        reason_text: str | None = None,
        edit: bool = False,
    ) -> None:
        ok, msg = await self.db.unregister(
            meeting_id,
            user_id,
            reason_type=reason_type,
            reason_text=reason_text,
        )
        self._clear_leave_pending(context)
        if ok:
            meeting = await self.db.get_meeting(meeting_id)
            when = format_meeting_time(meeting, self.local_tz, style="short") if meeting else None
            text = (
                f"✅ Твоё участие в встрече #{meeting_id}"
                + (f" «{meeting.topic}» ({when})" if meeting and when else "")
                + " отменено."
                + gcal_update_reminder("ru")
            )
            if edit:
                await message.edit_text(text, parse_mode="HTML")
            else:
                await message.reply_text(text, parse_mode="HTML")
            if meeting:
                db_user = await self.db.get_user(user_id)
                if db_user:
                    confirmed = await self.db.count_confirmed(meeting_id)
                    await self.scheduler.on_participant_change(meeting, db_user, "left", confirmed)
                await self._process_waitlist_after_unregister(meeting_id)
        else:
            err = f"Не удалось отменить участие: {msg}"
            if edit:
                await message.edit_text(err)
            else:
                await message.reply_text(err)

    async def _handle_leave_other_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return
        pending = context.user_data.get(LEAVE_OTHER_PENDING_KEY)
        if not pending:
            return
        meeting_id = pending.get("meeting_id")
        if meeting_id is None:
            self._clear_leave_pending(context)
            return

        text = (message.text or "").strip()
        if not text:
            await message.reply_text("Пожалуйста, напиши короткую причину или нажми «Отмена».")
            return
        if len(text) > MAX_OTHER_REASON_LEN:
            await message.reply_text(
                f"Слишком длинно. Пожалуйста, уложись в {MAX_OTHER_REASON_LEN} символов."
            )
            return

        result = await preflight_leave(self.db, meeting_id, user.id)
        if result.error:
            self._clear_leave_pending(context)
            await self._reply_leave_preflight_error(message, result.error)
            return

        context.user_data[LEAVE_OTHER_TEXT_KEY] = text
        await self._show_final_leave_confirm(
            message, meeting_id, CancellationReasonType.OTHER
        )

    async def _complete_registration(self, message, meeting_id: int, user_id: int, db_user: User) -> None:
        ok, msg, reg_status = await self.db.register(
            meeting_id, user_id, local_tz=self.local_tz
        )
        if ok and reg_status == RegistrationStatus.CONFIRMED:
            await message.reply_text(msg)
            meeting = await self.db.get_meeting(meeting_id)
            if meeting:
                confirmed = await self.db.count_confirmed(meeting_id)
                await self.scheduler.on_participant_change(meeting, db_user, "joined", confirmed)
                await self._send_google_calendar_offer(message, meeting, lang="ru")
            return
        if msg == "Meeting is full. Join the waitlist if a spot opens.":
            await message.reply_text(format_full(), reply_markup=build_full_keyboard(meeting_id))
        elif msg == "Meeting not found or canceled.":
            await message.reply_text(format_unavailable(), reply_markup=build_unavailable_keyboard())
        else:
            await message.reply_text(msg)

    async def _complete_offer_accept(self, message, entry_id: int, user_id: int, db_user: User) -> None:
        now_utc = datetime.now(tz.UTC)
        result = await self.waitlist.accept_offer(entry_id, user_id, now_utc)
        await message.reply_text(result.message)
        if result.ok and result.entry:
            meeting = await self.db.get_meeting(result.entry.meeting_id)
            if meeting:
                confirmed = await self.db.count_confirmed(meeting.id)
                await self.scheduler.on_participant_change(meeting, db_user, "joined", confirmed)
                await self._send_google_calendar_offer(message, meeting, lang="ru")

    async def _get_meeting_user_context(
        self, meeting_id: int, user_id: int | None, *, include_register: bool = True
    ) -> tuple[int, bool, str | None]:
        available = await self.db.available_spots(meeting_id)
        is_participant = False
        waitlist_state = None
        if user_id is not None:
            is_participant = await self.db.is_registered(meeting_id, user_id)
            waitlist_state = await self.waitlist.get_user_waitlist_state(meeting_id, user_id)
        return available, is_participant, waitlist_state

    def _google_calendar_url(self, meeting: Meeting) -> str | None:
        return build_google_calendar_event_url(
            meeting,
            local_tz=self.local_tz,
            bot_username=self.bot_username,
        )

    def _google_calendar_keyboard(
        self, meeting: Meeting, *, lang: Literal["en", "ru"] = "en"
    ) -> InlineKeyboardMarkup | None:
        url = self._google_calendar_url(meeting)
        if not url:
            return None
        return google_calendar_keyboard(url, lang=lang)

    async def _send_google_calendar_offer(
        self, message, meeting: Meeting, *, lang: Literal["en", "ru"]
    ) -> None:
        offer = build_calendar_offer(
            meeting,
            local_tz=self.local_tz,
            bot_username=self.bot_username,
            lang=lang,
        )
        if not offer:
            return
        text, keyboard = offer
        await message.reply_text(text, reply_markup=keyboard)

    def _upcoming_meetings_fallback_keyboard(self) -> InlineKeyboardMarkup:
        """Keyboard shown when a meeting deep link cannot be opened."""
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(text="Все предстоящие встречи", callback_data="show_upcoming"),
        ]])

    async def _format_upcoming_meeting_summary(self, meeting: Meeting) -> tuple[str, int, int, int]:
        """Build the list-entry text and seat counts for an upcoming meeting."""
        when = format_meeting_time(meeting, self.local_tz, style="iso")
        host_name = await self.db.get_user_name(meeting.created_by) or "Unknown"
        confirmed = await self.db.count_confirmed(meeting.id)
        hosts = await self.db.count_hosts(meeting.id)
        available = await self.db.available_spots(meeting.id)
        text = (
            f"<b>{when}</b>\n"
            f"<b>{meeting.topic}</b>\n"
            f"Ведет: {host_name}\n"
            f"📍 {meeting.location or 'TBA'}\n"
            f"Свободных мест: {available} (+ведущих: {hosts})"
        )
        return text, available, confirmed, hosts

    async def _send_waitlist_offer_dms(self, notifications: list[OfferNotification]) -> None:
        if not self.scheduler._bot:
            return
        await send_offer_dms(self.scheduler._bot, self.waitlist, notifications)

    async def _process_waitlist_after_unregister(self, meeting_id: int) -> None:
        now_utc = datetime.now(tz.UTC)
        notifications = await self.waitlist.process_available_spots(meeting_id, now_utc)
        await self._send_waitlist_offer_dms(notifications)

    async def _send_upcoming_meeting_entry(
        self,
        message,
        meeting: Meeting,
        user_id: int | None,
        *,
        include_register: bool = True,
    ) -> None:
        """Send one upcoming-meeting card with contextual action buttons."""
        text, available, _, _ = await self._format_upcoming_meeting_summary(meeting)
        now_utc = datetime.now(tz.UTC)
        reg_open = is_registration_open(meeting, now_utc)
        _, is_participant, waitlist_state = await self._get_meeting_user_context(
            meeting.id, user_id, include_register=include_register
        )
        waitlist_count = 0
        if user_id is not None and meeting.created_by == user_id:
            waitlist_count = await self.db.count_active_waitlist(meeting.id)
        keyboard = self._build_meeting_actions_keyboard(
            meeting.id,
            user_id,
            meeting.created_by,
            include_register=include_register,
            is_participant=is_participant,
            available=available,
            waitlist_state=waitlist_state,
            waitlist_count=waitlist_count,
            registration_open=reg_open,
        )
        await message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

    async def _send_welcome_message(self, message, *, user_id: int | None = None) -> None:
        """Send the default /start welcome text."""
        await message.reply_text(
            WELCOME_MESSAGE,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=self._main_menu_markup(user_id),
        )

    async def _handle_meeting_deep_link(self, update: Update, public_token: str) -> None:
        """Open a meeting from a channel deep link."""
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return

        try:
            meeting = await self.db.get_meeting_by_public_token(public_token)
        except Exception:
            logger.exception("Failed to load meeting by public token")
            await message.reply_text(
                "Не удалось открыть встречу. Попробуй позже или посмотри все предстоящие встречи.",
                reply_markup=self._upcoming_meetings_fallback_keyboard(),
            )
            return

        if not meeting:
            await message.reply_text(
                "Встреча не найдена. Возможно, ссылка устарела.",
                reply_markup=self._upcoming_meetings_fallback_keyboard(),
            )
            return

        if meeting.canceled_at is not None:
            await message.reply_text(
                "Эта встреча отменена и больше недоступна.",
                reply_markup=self._upcoming_meetings_fallback_keyboard(),
            )
            return

        now_utc = datetime.now(tz.UTC)
        if ensure_utc(meeting.start_at_utc) < now_utc:
            await message.reply_text(
                "Эта встреча уже прошла.",
                reply_markup=self._upcoming_meetings_fallback_keyboard(),
            )
            return

        is_host = meeting.created_by == user.id
        if not is_host and not is_registration_open(meeting, now_utc):
            when = ensure_utc(meeting.registration_starts_at_utc).astimezone(self.local_tz)
            await message.reply_text(
                f"Регистрация на эту встречу откроется {when:%d.%m.%Y %H:%M}.",
                reply_markup=self._upcoming_meetings_fallback_keyboard(),
            )
            return

        await self._send_upcoming_meeting_entry(message, meeting, user.id, include_register=True)

    @staticmethod
    def _format_participant_list(meeting: Meeting, rows, max_participants: int) -> str:
        """Format a numbered participant list for host display."""
        if not rows:
            return (
                f"<b>Участники встречи «{meeting.topic}»</b>\n\n"
                f"Пока никто не записался.\n"
                f"Итого: 0 / {max_participants}"
            )
        lines = [f"<b>Участники встречи «{meeting.topic}»</b>\n"]
        for i, row in enumerate(rows, start=1):
            _, user = row
            name = user.name or ""
            username_part = f" (@{user.username})" if user.username else ""
            lines.append(f"{i}. {name}{username_part}")
        lines.append(f"\nИтого: {len(rows)} / {max_participants}")
        return "\n".join(lines)

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
                InlineKeyboardButton(text="Время окончания", callback_data="edit_field:endtime"),
                InlineKeyboardButton(text="Начало регистрации", callback_data="edit_field:regstart"),
            ],
            [
                InlineKeyboardButton(text="Макс. участников", callback_data="edit_field:max"),
                InlineKeyboardButton(text="Место", callback_data="edit_field:location"),
            ],
            [
                InlineKeyboardButton(text="Фото", callback_data="edit_field:photo"),
            ],
            [
                InlineKeyboardButton(text="✅ Готово", callback_data="edit_field:done"),
            ],
        ])

    def _format_edit_menu_text(self, meeting: Meeting, notice: str = "") -> str:
        """Format the current meeting state as an edit-menu message."""
        when = format_meeting_time(meeting, self.local_tz, style="short")
        reg_start = format_registration_start(meeting, self.local_tz)
        photo_line = "🖼 Фото: есть" if meeting.photo_file_id else "🖼 Фото: нет"
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            f"✏️ <b>Редактирование встречи #{meeting.id}</b>\n\n"
            f"📌 Тема: {meeting.topic}\n\n"
            f"📝 Описание: {meeting.description}\n\n"
            f"📅 Дата и время: {when}\n"
            f"🔓 Регистрация откроется: {reg_start}\n"
            f"👥 Макс. участников: {meeting.max_participants}\n"
            f"📍 Место: {meeting.location or 'не указано'}\n"
            f"{photo_line}\n\n"
            "Выбери, что хочешь изменить:"
        )

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

        if meeting.canceled_at:
            await cq.message.reply_text("Эта встреча уже отменена.")
            return ConversationHandler.END

        context.user_data["edit_meeting_id"] = meeting_id
        context.user_data["edit_snapshot"] = snapshot_meeting(meeting)
        await cq.message.reply_text(
            self._format_edit_menu_text(meeting) + _CONVERSATION_ESCAPE_HINT,
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML",
        )
        with contextlib.suppress(TelegramError):
            await self._hide_main_menu(cq.message)
        return self.STATE_EDIT_MENU

    async def _edit_done(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Finish editing and show confirmation."""
        cq = update.callback_query
        if cq:
            await cq.answer()

        meeting_id = context.user_data.get("edit_meeting_id")
        if not meeting_id and cq and cq.message and cq.message.text:
            match = re.search(r"#(\d+)", cq.message.text)
            if match:
                meeting_id = int(match.group(1))
        if meeting_id:
            meeting = await self.db.get_meeting(meeting_id)
            if meeting:
                snapshot = context.user_data.get("edit_snapshot")
                if snapshot and self.scheduler._bot:
                    changes = detect_important_changes(snapshot, meeting)
                    if changes:
                        user = update.effective_user
                        host_id = user.id if user else meeting.created_by
                        await notify_participants(
                            self.scheduler._bot,
                            self.db,
                            meeting,
                            changes,
                            exclude_user_id=host_id,
                            local_tz=self.local_tz,
                            bot_username=self.bot_username,
                        )
                when = format_meeting_time(meeting, self.local_tz, style="short")
                reg_start = format_registration_start(meeting, self.local_tz)
                photo_line = f"\n🖼 Фото: {'есть' if meeting.photo_file_id else 'нет'}"
                text = (
                    f"✅ Редактирование встречи #{meeting_id} завершено.\n\n"
                    f"📌 Тема: {meeting.topic}\n\n"
                    f"📝 Описание: {meeting.description}\n\n"
                    f"📅 Дата и время: {when}\n"
                    f"🔓 Регистрация откроется: {reg_start}\n"
                    f"📍 Место: {meeting.location or 'не указано'}\n"
                    f"👥 Макс. участников: {meeting.max_participants}"
                    f"{photo_line}"
                    f"{gcal_update_reminder('ru')}"
                )
                user = update.effective_user
                if cq:
                    await cq.edit_message_text(text, parse_mode="HTML")
                    await self._restore_main_menu(cq.message, user.id if user else None)
                else:
                    await update.effective_message.reply_text(
                        text,
                        parse_mode="HTML",
                        reply_markup=self._main_menu_markup(user.id if user else None),
                    )

        context.user_data.clear()
        return ConversationHandler.END

    async def _edit_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel the edit operation."""
        return await self._finish_conversation_cancel(
            update, context, message=_EDIT_CANCEL_MESSAGE
        )

    # ----- Field selection handlers -----

    async def _edit_select_topic(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit the topic."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()
        await cq.message.reply_text(
            "Введи новую тему встречи:",
            reply_markup=remove_main_menu_keyboard(),
        )
        return self.STATE_EDIT_TOPIC

    async def _edit_select_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit the description."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()
        await cq.message.reply_text(
            "Введи новое описание встречи:",
            reply_markup=remove_main_menu_keyboard(),
        )
        return self.STATE_EDIT_DESCRIPTION

    async def _edit_select_max(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit max participants."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()

        meeting_id = context.user_data.get("edit_meeting_id")
        confirmed = await self.db.count_confirmed(meeting_id) if meeting_id else 0
        await cq.message.reply_text(
            f"Введи новое максимальное количество участников.\n"
            f"(Сейчас зарегистрировано: {confirmed})",
            reply_markup=remove_main_menu_keyboard(),
        )
        return self.STATE_EDIT_MAX

    async def _edit_select_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit the location."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()
        await cq.message.reply_text(
            "Введи новое место проведения встречи:\n"
            "(Отправь '-' чтобы убрать место)",
            reply_markup=remove_main_menu_keyboard(),
        )
        return self.STATE_EDIT_LOCATION

    async def _edit_select_datetime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit date/time - show month picker."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()
        context.user_data.pop("edit_picking_reg_start", None)
        await cq.edit_message_text(
            "📅 Выбери новый месяц встречи:",
            reply_markup=MonthPickerKeyboard.build()
        )
        return self.STATE_EDIT_MONTH

    async def _edit_select_endtime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()

        meeting_id = context.user_data.get("edit_meeting_id")
        meeting = await self.db.get_meeting(meeting_id) if meeting_id else None
        if not meeting:
            await cq.edit_message_text("Встреча не найдена.")
            return ConversationHandler.END

        start_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        context.user_data["edit_end_date"] = start_local.date()
        await cq.edit_message_text(
            f"📅 Дата: {start_local:%d.%m.%Y}\n\n🕐 Выбери час окончания встречи:",
            reply_markup=TimePickerKeyboard.build_hours(),
        )
        return self.STATE_EDIT_END_HOUR

    async def _edit_select_regstart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()
        await cq.edit_message_text(
            "🔓 Когда открыть регистрацию?",
            reply_markup=self._reg_start_choice_keyboard("edit_reg_start"),
        )
        return self.STATE_EDIT_REG_START_CHOICE

    async def _edit_reg_start_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_REG_START_CHOICE
        await cq.answer()

        meeting_id = context.user_data.get("edit_meeting_id")
        data = cq.data or ""

        if data == "edit_reg_start:now":
            meeting = await self.db.update_meeting(meeting_id, clear_registration_start=True)
            if not meeting:
                await cq.edit_message_text("Встреча не найдена.")
                return ConversationHandler.END
            await cq.edit_message_text(
                self._format_edit_menu_text(meeting, "✅ Начало регистрации обновлено!"),
                reply_markup=self._build_edit_menu_keyboard(),
                parse_mode="HTML",
            )
            return self.STATE_EDIT_MENU

        if data == "edit_reg_start:pick":
            context.user_data["edit_picking_reg_start"] = True
            await cq.edit_message_text(
                "📅 Выбери месяц открытия регистрации:",
                reply_markup=MonthPickerKeyboard.build(),
            )
            return self.STATE_EDIT_MONTH

        return self.STATE_EDIT_REG_START_CHOICE

    async def _edit_select_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """User selected to edit the meeting photo."""
        cq = update.callback_query
        if not cq:
            return self.STATE_EDIT_MENU
        await cq.answer()

        meeting_id = context.user_data.get("edit_meeting_id")
        meeting = await self.db.get_meeting(meeting_id) if meeting_id else None
        if not meeting:
            await cq.message.reply_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        if meeting.photo_file_id:
            await cq.message.reply_photo(
                meeting.photo_file_id,
                caption="Текущее фото встречи. Отправь новое, чтобы заменить.",
                reply_markup=edit_photo_keyboard(has_photo=True),
            )
        else:
            await cq.message.reply_text(
                "📸 Отправь фото для встречи.",
                reply_markup=edit_photo_keyboard(has_photo=False),
            )
        return self.STATE_EDIT_PHOTO

    async def _edit_photo_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo upload, removal, or cancel in the edit-photo state."""
        meeting_id = context.user_data.get("edit_meeting_id")
        if not meeting_id:
            await update.effective_message.reply_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        cq = update.callback_query
        if cq:
            await cq.answer()
            if cq.data == "edit_photo:remove":
                meeting = await self.db.update_meeting(meeting_id, clear_photo=True)
                if not meeting:
                    await cq.message.reply_text("Встреча не найдена.")
                    return ConversationHandler.END
                notice = "✅ Фото удалено!"
            else:  # edit_photo:cancel
                meeting = await self.db.get_meeting(meeting_id)
                if not meeting:
                    await cq.message.reply_text("Встреча не найдена.")
                    return ConversationHandler.END
                notice = ""
            await cq.message.reply_text(
                self._format_edit_menu_text(meeting, notice),
                reply_markup=self._build_edit_menu_keyboard(),
                parse_mode="HTML",
            )
            return self.STATE_EDIT_MENU

        # Photo message
        file_id = update.message.photo[-1].file_id
        meeting = await self.db.update_meeting(meeting_id, photo_file_id=file_id)
        if not meeting:
            await update.message.reply_text("Встреча не найдена.")
            return ConversationHandler.END

        await update.message.reply_text(
            self._format_edit_menu_text(meeting, "✅ Фото обновлено!"),
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML",
        )
        return self.STATE_EDIT_MENU

    async def _edit_photo_invalid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reject non-photo messages in the edit-photo state."""
        await update.effective_message.reply_text("Пожалуйста, отправь фото или используй кнопки.")
        return self.STATE_EDIT_PHOTO

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

        await update.effective_message.reply_text(
            self._format_edit_menu_text(meeting, "✅ Тема обновлена!"),
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML",
        )
        return self.STATE_EDIT_MENU

    async def _edit_description_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle new description input."""
        message = update.effective_message
        if not (message.text or "").strip():
            await update.effective_message.reply_text("Описание не может быть пустым. Введи новое описание:")
            return self.STATE_EDIT_DESCRIPTION

        description = message_text_as_html(message)
        meeting_id = context.user_data.get("edit_meeting_id")
        if not meeting_id:
            await update.effective_message.reply_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        meeting = await self.db.update_meeting(meeting_id, description=description)
        if not meeting:
            await update.effective_message.reply_text("Встреча не найдена.")
            return ConversationHandler.END

        await update.effective_message.reply_text(
            self._format_edit_menu_text(meeting, "✅ Описание обновлено!"),
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML",
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

        await update.effective_message.reply_text(
            self._format_edit_menu_text(meeting, "✅ Максимум участников обновлён!"),
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML",
        )
        return self.STATE_EDIT_MENU

    async def _edit_location_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle new location input."""
        message = update.effective_message
        raw = (message.text or "").strip()
        meeting_id = context.user_data.get("edit_meeting_id")

        if not meeting_id:
            await update.effective_message.reply_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        skip_values = {"-", "—", "пропустить", "нет", "убрать"}
        if raw.lower() in skip_values:
            meeting = await self.db.update_meeting(meeting_id, clear_location=True)
        else:
            meeting = await self.db.update_meeting(meeting_id, location=message_text_as_html(message))
        if not meeting:
            await update.effective_message.reply_text("Встреча не найдена.")
            return ConversationHandler.END

        await update.effective_message.reply_text(
            self._format_edit_menu_text(meeting, "✅ Место обновлено!"),
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML",
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

        picking_reg = context.user_data.get("edit_picking_reg_start")
        tomorrow = date.today() + timedelta(days=1)
        today = date.today()
        first_of_month = date(year, month, 1)
        min_date_val = max(first_of_month, today if picking_reg else tomorrow)
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
        day_label = "день открытия регистрации" if picking_reg else "новый день встречи"
        await query.edit_message_text(
            f"📅 Выбери {day_label}:",
            reply_markup=append_cancel_row(calendar),
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
        picking_reg = context.user_data.get("edit_picking_reg_start")
        tomorrow = date.today() + timedelta(days=1)
        today = date.today()
        first_of_month = date(year, month, 1)
        min_date_val = max(first_of_month, today if picking_reg else tomorrow)
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
            day_label = "день открытия регистрации" if picking_reg else "новый день встречи"
            await query.edit_message_text(
                f"📅 Выбери {day_label}:",
                reply_markup=append_cancel_row(key),
            )
            return self.STATE_EDIT_DATE

        if result:
            context.user_data["edit_selected_date"] = result
            if picking_reg:
                await query.edit_message_text(
                    f"📅 Дата: {result:%d.%m.%Y}\n\n🕐 Выбери час открытия регистрации:",
                    reply_markup=TimePickerKeyboard.build_hours(),
                )
            else:
                await query.edit_message_text(
                    f"📅 Дата: {result:%d.%m.%Y}\n\n🕐 Выбери час начала встречи:",
                    reply_markup=TimePickerKeyboard.build_hours(),
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
        picking_reg = context.user_data.get("edit_picking_reg_start")
        hour_label = "открытия регистрации" if picking_reg else "начала встречи"
        try:
            _, hour_str = data.split(":", 1)
            if hour_str == "back":
                await query.edit_message_text(
                    f"📅 Дата: {context.user_data.get('edit_selected_date'):%d.%m.%Y}\n\n"
                    f"🕐 Выбери час {hour_label}:",
                    reply_markup=TimePickerKeyboard.build_hours(),
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
        value_utc = local_dt.astimezone(tz.UTC)

        if context.user_data.get("edit_picking_reg_start"):
            meeting = await self.db.get_meeting(meeting_id)
            if meeting and value_utc > ensure_utc(meeting.start_at_utc):
                await query.edit_message_text(
                    "Время открытия регистрации не может быть позже начала встречи. "
                    "Выбери другое время:",
                    reply_markup=TimePickerKeyboard.build_hours(),
                )
                return self.STATE_EDIT_HOUR
            meeting = await self.db.update_meeting(
                meeting_id, registration_starts_at_utc=value_utc
            )
            context.user_data.pop("edit_picking_reg_start", None)
            notice = "✅ Начало регистрации обновлено!"
        else:
            meeting = await self.db.update_meeting(meeting_id, start_at_utc=value_utc)
            notice = "✅ Дата и время обновлены!"

        if not meeting:
            await query.edit_message_text("Встреча не найдена.")
            return ConversationHandler.END

        await query.edit_message_text(
            self._format_edit_menu_text(meeting, notice),
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML",
        )
        return self.STATE_EDIT_MENU

    async def _edit_end_hour_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query:
            return self.STATE_EDIT_END_HOUR
        await query.answer()

        data = query.data or ""
        edit_end_date = context.user_data.get("edit_end_date")
        try:
            _, hour_str = data.split(":", 1)
            if hour_str == "back":
                await query.edit_message_text(
                    f"📅 Дата: {edit_end_date:%d.%m.%Y}\n\n🕐 Выбери час окончания встречи:",
                    reply_markup=TimePickerKeyboard.build_hours(),
                )
                return self.STATE_EDIT_END_HOUR
            hour = int(hour_str)
        except Exception:
            await query.edit_message_text("Ошибка при выборе часа. Попробуй ещё раз.")
            return self.STATE_EDIT_END_HOUR

        context.user_data["edit_selected_end_hour"] = hour
        await query.edit_message_text(
            f"📅 Дата: {edit_end_date:%d.%m.%Y}\n"
            f"🕐 Час окончания: {hour:02d}:00\n\n"
            "⏱ Выбери минуты:",
            reply_markup=TimePickerKeyboard.build_minutes(hour),
        )
        return self.STATE_EDIT_END_MINUTE

    async def _edit_end_minute_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query:
            return self.STATE_EDIT_END_MINUTE
        await query.answer()

        data = query.data or ""
        edit_end_date = context.user_data.get("edit_end_date")
        meeting_id = context.user_data.get("edit_meeting_id")

        if data == "hour:back":
            await query.edit_message_text(
                f"📅 Дата: {edit_end_date:%d.%m.%Y}\n\n🕐 Выбери час окончания встречи:",
                reply_markup=TimePickerKeyboard.build_hours(),
            )
            return self.STATE_EDIT_END_HOUR

        try:
            _, hour_str, minute_str = data.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
        except Exception:
            await query.edit_message_text("Ошибка при выборе времени. Попробуй ещё раз.")
            return self.STATE_EDIT_END_MINUTE

        if not edit_end_date or not meeting_id:
            await query.edit_message_text("Сессия истекла. Начни редактирование заново.")
            return ConversationHandler.END

        end_local = datetime(
            year=edit_end_date.year,
            month=edit_end_date.month,
            day=edit_end_date.day,
            hour=hour,
            minute=minute,
            tzinfo=self.local_tz,
        )
        meeting = await self.db.get_meeting(meeting_id)
        if not meeting:
            await query.edit_message_text("Встреча не найдена.")
            return ConversationHandler.END

        start_local = ensure_utc(meeting.start_at_utc).astimezone(self.local_tz)
        if end_local <= start_local:
            await query.edit_message_text(
                "Время окончания должно быть позже начала. Выбери другое время:",
                reply_markup=TimePickerKeyboard.build_hours(),
            )
            return self.STATE_EDIT_END_HOUR

        meeting = await self.db.update_meeting(
            meeting_id, end_at_utc=end_local.astimezone(tz.UTC)
        )
        if not meeting:
            await query.edit_message_text("Встреча не найдена.")
            return ConversationHandler.END

        await query.edit_message_text(
            self._format_edit_menu_text(meeting, "✅ Время окончания обновлено!"),
            reply_markup=self._build_edit_menu_keyboard(),
            parse_mode="HTML",
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
            "Создаём новую встречу! Как она называется?" + _CONVERSATION_ESCAPE_HINT,
            reply_markup=remove_main_menu_keyboard(),
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
        message = update.effective_message
        if not (message.text or "").strip():
            await update.effective_message.reply_text("Пожалуйста, напиши описание встречи")
            return self.STATE_DESCRIPTION
        context.user_data["description"] = message_text_as_html(message)
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
        message = update.effective_message
        raw = (message.text or "").strip()
        skip_values = {"", "-", "—", "пропустить", "нет"}
        location = None if raw.lower() in skip_values else message_text_as_html(message)
        context.user_data["location"] = location
        received = "не указано" if not location else location
        await update.effective_message.reply_text(
            f"Принято! Место проведения: {received}.\n\n"
            f"📅 Выбери месяц встречи:",
            reply_markup=MonthPickerKeyboard.build(),
            parse_mode="HTML",
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
        picking_reg = context.user_data.get("picking_reg_start")
        tomorrow = date.today() + timedelta(days=1)
        today = date.today()
        first_of_month = date(year, month, 1)
        min_date_val = max(first_of_month, today if picking_reg else tomorrow)
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
        day_label = "день открытия регистрации" if picking_reg else "день встречи"
        await query.edit_message_text(
            f"📅 Выбери {day_label}:",
            reply_markup=append_cancel_row(calendar),
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
        picking_reg = context.user_data.get("picking_reg_start")
        tomorrow = date.today() + timedelta(days=1)
        today = date.today()
        first_of_month = date(year, month, 1)
        min_date_val = max(first_of_month, today if picking_reg else tomorrow)
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
            day_label = "день открытия регистрации" if picking_reg else "день встречи"
            await query.edit_message_text(
                f"📅 Выбери {day_label}:",
                reply_markup=append_cancel_row(key),
            )
            return self.STATE_DATE

        if result:
            context.user_data["selected_date"] = result
            if picking_reg:
                await query.edit_message_text(
                    f"📅 Дата: {result:%d.%m.%Y}\n\n🕐 Выбери час открытия регистрации:",
                    reply_markup=TimePickerKeyboard.build_hours(),
                )
            else:
                await query.edit_message_text(
                    f"📅 Дата: {result:%d.%m.%Y}\n\n🕐 Выбери час начала встречи:",
                    reply_markup=TimePickerKeyboard.build_hours(),
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
        picking_reg = context.user_data.get("picking_reg_start")
        hour_label = "открытия регистрации" if picking_reg else "начала встречи"
        try:
            _, hour_str = data.split(":", 1)
            if hour_str == "back":
                await query.edit_message_text(
                    f"📅 Дата: {context.user_data.get('selected_date'):%d.%m.%Y}\n\n"
                    f"🕐 Выбери час {hour_label}:",
                    reply_markup=TimePickerKeyboard.build_hours(),
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

        local_dt = datetime(
            year=selected_date.year,
            month=selected_date.month,
            day=selected_date.day,
            hour=hour,
            minute=minute,
            tzinfo=self.local_tz
        )

        if context.user_data.get("picking_reg_start"):
            start_utc = context.user_data.get("selected_start_utc")
            reg_utc = local_dt.astimezone(tz.UTC)
            if start_utc and reg_utc > start_utc:
                await query.edit_message_text(
                    "Время открытия регистрации не может быть позже начала встречи. "
                    "Выбери другое время:",
                    reply_markup=TimePickerKeyboard.build_hours(),
                )
                return self.STATE_HOUR
            context.user_data["selected_reg_start_utc"] = reg_utc
            context.user_data.pop("picking_reg_start", None)
            await query.edit_message_text(
                "📸 Хочешь добавить фото к встрече? (необязательно)\n"
                "Отправь фото или нажми «Пропустить».",
                reply_markup=photo_skip_keyboard(),
            )
            return self.STATE_PHOTO

        context.user_data["selected_start_utc"] = local_dt.astimezone(tz.UTC)
        await query.edit_message_text(
            f"📅 Дата: {selected_date:%d.%m.%Y}\n\n🕐 Выбери час окончания встречи:",
            reply_markup=TimePickerKeyboard.build_hours(),
        )
        return self.STATE_END_HOUR

    async def _create_meeting_end_hour_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query:
            return self.STATE_END_HOUR
        await query.answer()

        data = query.data or ""
        selected_date = context.user_data.get("selected_date")
        try:
            _, hour_str = data.split(":", 1)
            if hour_str == "back":
                await query.edit_message_text(
                    f"📅 Дата: {selected_date:%d.%m.%Y}\n\n🕐 Выбери час окончания встречи:",
                    reply_markup=TimePickerKeyboard.build_hours(),
                )
                return self.STATE_END_HOUR
            hour = int(hour_str)
        except Exception:
            await query.edit_message_text("Ошибка при выборе часа. Попробуй ещё раз.")
            return self.STATE_END_HOUR

        context.user_data["selected_end_hour"] = hour
        await query.edit_message_text(
            f"📅 Дата: {selected_date:%d.%m.%Y}\n"
            f"🕐 Час окончания: {hour:02d}:00\n\n"
            "⏱ Выбери минуты:",
            reply_markup=TimePickerKeyboard.build_minutes(hour),
        )
        return self.STATE_END_MINUTE

    async def _create_meeting_end_minute_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query:
            return self.STATE_END_MINUTE
        await query.answer()

        data = query.data or ""
        if data == "hour:back":
            selected_date = context.user_data.get("selected_date")
            await query.edit_message_text(
                f"📅 Дата: {selected_date:%d.%m.%Y}\n\n🕐 Выбери час окончания встречи:",
                reply_markup=TimePickerKeyboard.build_hours(),
            )
            return self.STATE_END_HOUR

        try:
            _, hour_str, minute_str = data.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
        except Exception:
            await query.edit_message_text("Ошибка при выборе времени. Попробуй ещё раз.")
            return self.STATE_END_MINUTE

        selected_date = context.user_data.get("selected_date")
        start_utc = context.user_data.get("selected_start_utc")
        if not selected_date or not start_utc:
            await query.edit_message_text("Ошибка: дата не выбрана. Начни заново с /create_meeting")
            return ConversationHandler.END

        end_local = datetime(
            year=selected_date.year,
            month=selected_date.month,
            day=selected_date.day,
            hour=hour,
            minute=minute,
            tzinfo=self.local_tz,
        )
        start_local = ensure_utc(start_utc).astimezone(self.local_tz)
        if end_local <= start_local:
            await query.edit_message_text(
                "Время окончания должно быть позже начала. Выбери другое время:",
                reply_markup=TimePickerKeyboard.build_hours(),
            )
            return self.STATE_END_HOUR

        context.user_data["selected_end_utc"] = end_local.astimezone(tz.UTC)
        await query.edit_message_text(
            "🔓 Когда открыть регистрацию?",
            reply_markup=self._reg_start_choice_keyboard("reg_start"),
        )
        return self.STATE_REG_START_CHOICE

    def _reg_start_choice_keyboard(self, prefix: str) -> InlineKeyboardMarkup:
        return append_cancel_row(InlineKeyboardMarkup([[
            InlineKeyboardButton(text="Сразу", callback_data=f"{prefix}:now"),
            InlineKeyboardButton(text="Выбрать дату", callback_data=f"{prefix}:pick"),
        ]]))

    async def _create_reg_start_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query:
            return self.STATE_REG_START_CHOICE
        await query.answer()

        data = query.data or ""
        if data == "reg_start:now":
            context.user_data["selected_reg_start_utc"] = None
            await query.edit_message_text(
                "📸 Хочешь добавить фото к встрече? (необязательно)\n"
                "Отправь фото или нажми «Пропустить».",
                reply_markup=photo_skip_keyboard(),
            )
            return self.STATE_PHOTO

        if data == "reg_start:pick":
            context.user_data["picking_reg_start"] = True
            await query.edit_message_text(
                "📅 Выбери месяц открытия регистрации:",
                reply_markup=MonthPickerKeyboard.build(),
            )
            return self.STATE_MONTH

        return self.STATE_REG_START_CHOICE

    async def _create_meeting_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await self._finish_conversation_cancel(
            update, context, message=_CREATE_CANCEL_MESSAGE
        )

    async def _create_meeting_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo upload or skip, then create the meeting."""
        user = update.effective_user
        if not user:
            return ConversationHandler.END

        cq = update.callback_query
        if cq:
            await cq.answer()
            photo_file_id = None  # user skipped
        else:
            photo_file_id = update.message.photo[-1].file_id

        start_utc = context.user_data.get("selected_start_utc")
        topic = context.user_data.get("topic")
        description = context.user_data.get("description")
        max_participants = context.user_data.get("max_participants")
        location = context.user_data.get("location")

        start_utc = context.user_data.get("selected_start_utc")
        end_utc = context.user_data.get("selected_end_utc")
        reg_start_utc = context.user_data.get("selected_reg_start_utc")

        if not all([start_utc, topic, description, max_participants]):
            target = cq.message if cq else update.message
            await target.reply_text("Произошла ошибка. Начни заново с /create_meeting")
            context.user_data.clear()
            return ConversationHandler.END

        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        meeting = await self.db.create_meeting(
            host_id=user.id,
            topic=topic,
            description=description,
            start_at_utc=start_utc,
            end_at_utc=end_utc,
            registration_starts_at_utc=reg_start_utc,
            max_participants=max_participants,
            location=location,
            photo_file_id=photo_file_id,
        )
        await self.scheduler.maybe_announce_new_meeting(meeting)

        when = format_meeting_time(meeting, self.local_tz, style="short")
        reg_start = format_registration_start(meeting, self.local_tz)
        photo_line = "\n🖼 Фото: прикреплено" if photo_file_id else ""
        calendar_keyboard = self._google_calendar_keyboard(meeting, lang="ru")
        disclaimer = f"\n\n{gcal_disclaimer('ru')}" if calendar_keyboard else ""
        summary = (
            "✅ Спасибо! Встреча создана.\n\n"
            f"📌 Название: {meeting.topic}\n\n"
            f"📝 Описание: {meeting.description}\n\n"
            f"📅 Дата и время (Берлин): {when}\n"
            f"🔓 Регистрация откроется: {reg_start}\n"
            f"📍 Место: {meeting.location or 'не указано'}\n"
            f"👥 Максимум участников: {meeting.max_participants}"
            f"{photo_line}\n"
            f"🆔 ID встречи: #{meeting.id}"
            f"{disclaimer}"
        )
        if cq:
            await cq.edit_message_text(summary, reply_markup=calendar_keyboard, parse_mode="HTML")
            await self._restore_main_menu(cq.message, user.id)
        else:
            await update.message.reply_text(summary, reply_markup=calendar_keyboard, parse_mode="HTML")
            await self._restore_main_menu(update.message, user.id)

        context.user_data.clear()
        return ConversationHandler.END

    async def _create_meeting_photo_invalid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reject non-photo messages in the photo step."""
        await update.effective_message.reply_text(
            "Пожалуйста, отправь фото или нажми «Пропустить».",
            reply_markup=photo_skip_keyboard(),
        )
        return self.STATE_PHOTO

    # ========== Command Handlers ==========

    async def cmd_force_summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Manually trigger the announcement summary (admin-only)."""
        user = update.effective_user
        if not user or user.id not in self.settings.admin_user_ids:
            await update.effective_message.reply_text("Not authorized.")
            return
        if not self.settings.announcements_channel_id:
            await update.effective_message.reply_text("No announcements channel configured.")
            return
        await update.effective_message.reply_text("Sending announcement summary...")
        await self.scheduler.run_announcement_now()
        await update.effective_message.reply_text("Done.")

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start and /help: greet user and list commands."""
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        message = update.effective_message
        if not message:
            return

        if not await self._user_has_community_access(user.id, context):
            await self._send_restricted_access(update)
            return

        if context.args:
            raw_payload = context.args[0] if len(context.args) == 1 else " ".join(context.args)
            payload = parse_start_payload(raw_payload)
            if payload.type == "meeting" and payload.public_token:
                await self._handle_meeting_deep_link(update, payload.public_token)
                return
            if raw_payload.startswith("m_"):
                await message.reply_text(
                    "Неверная ссылка на встречу.",
                    reply_markup=self._upcoming_meetings_fallback_keyboard(),
                )
                return

        await self._send_welcome_message(message, user_id=user.id)

    async def handle_main_menu_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Route main-menu reply-keyboard taps to existing command handlers."""
        message = update.effective_message
        if not message or not message.text:
            return
        text = message.text.strip()
        if text == MENU_FORCE_SUMMARY:
            await self.cmd_force_summary(update, context)
            return
        if not await self._ensure_community_access(update, context):
            return
        if text == MENU_MEETINGS:
            await self.cmd_meetings(update, context)
        elif text == MENU_MY:
            await self.cmd_my(update, context)
        elif text == MENU_HELP:
            user = update.effective_user
            await self._send_welcome_message(message, user_id=user.id if user else None)

    async def handle_unknown_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reply to unrecognized plain text outside active conversations."""
        if context.user_data.get(LEAVE_OTHER_PENDING_KEY):
            if not await self._ensure_community_access(update, context):
                return
            await self._handle_leave_other_text(update, context)
            return
        if not await self._ensure_community_access(update, context):
            return
        message = update.effective_message
        if not message:
            return
        user = update.effective_user
        await message.reply_text(
            f"Не понимаю. Выбери действие в меню или нажми {MENU_HELP}.",
            reply_markup=self._main_menu_markup(user.id if user else None),
        )

    async def cmd_meetings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List all upcoming meetings with register/details buttons."""
        user = update.effective_user
        message = update.effective_message
        if not message:
            return
        now_utc = datetime.now(tz.UTC)
        meetings = await self.db.list_upcoming_meetings_visible(
            now_utc, user.id if user else None
        )
        if not meetings:
            await message.reply_text("No upcoming meetings.")
            return

        for m in meetings:
            await self._send_upcoming_meeting_entry(
                message, m, user.id if user else None, include_register=True
            )

    async def cb_show_upcoming(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline button to list all upcoming meetings."""
        cq = update.callback_query
        if not cq or not cq.message:
            return
        await cq.answer()
        user = update.effective_user
        now_utc = datetime.now(tz.UTC)
        meetings = await self.db.list_upcoming_meetings_visible(
            now_utc, user.id if user else None
        )
        if not meetings:
            await cq.message.reply_text("No upcoming meetings.")
            return
        for m in meetings:
            await self._send_upcoming_meeting_entry(
                cq.message, m, user.id if user else None, include_register=True
            )

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
            when = format_meeting_time(m, self.local_tz, style="iso")
            host_name = await self.db.get_user_name(m.created_by) or "Unknown"
            confirmed = await self.db.count_confirmed(m.id)
            hosts = await self.db.count_hosts(m.id)
            available = await self.db.available_spots(m.id)
            text = (
                f"<b>{when}</b>\n"
                f"<b>{m.topic}</b>\n"
                f"Ведет: {host_name}\n"
                f"📍 {m.location or 'TBA'}\n"
                f"Свободных мест: {available} (+ведущих: {hosts})"
            )
            waitlist_count = 0
            if m.created_by == user.id:
                waitlist_count = await self.db.count_active_waitlist(m.id)
            keyboard = self._build_meeting_actions_keyboard(
                m.id, user.id, m.created_by, is_participant=True, waitlist_count=waitlist_count
            )
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
        await self._start_registration_flow(update.effective_message, meeting_id, user.id)

    async def cmd_unregister(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Unregister current user from a meeting by ID."""
        user = update.effective_user
        if not user:
            return
        db_user = await self.db.get_or_create_user(user.id, user.full_name, user.username)
        if not context.args:
            await update.effective_message.reply_text("Usage: /unregister <meeting_id>")
            return
        try:
            meeting_id = int(context.args[0])
        except Exception:
            await update.effective_message.reply_text("Meeting id must be a number.")
            return
        ok, msg = await self.db.unregister(meeting_id, user.id)
        if ok:
            msg += gcal_update_reminder("en")
        await update.effective_message.reply_text(msg)
        if ok:
            meeting = await self.db.get_meeting(meeting_id)
            if meeting:
                confirmed = await self.db.count_confirmed(meeting_id)
                await self.scheduler.on_participant_change(meeting, db_user, "left", confirmed)
                await self._process_waitlist_after_unregister(meeting_id)

    # ========== Callback Query Handlers ==========

    async def cb_register(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline 'Записаться' button — start mindful confirmation flow."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            await cq.message.reply_text("Invalid registration request.")
            return
        await self._start_registration_flow(cq.message, meeting_id, user.id)

    async def cb_reg_s1_yes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            return
        meeting = await self.db.get_meeting(meeting_id)
        if meeting is None or meeting.canceled_at is not None:
            await cq.message.reply_text(
                format_unavailable(), reply_markup=build_unavailable_keyboard()
            )
            return
        await cq.message.reply_text(
            format_step2(), reply_markup=build_step2_keyboard(meeting_id)
        )

    async def cb_reg_s1_no(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            return
        await self._reply_registration_cancelled(cq.message, meeting_id)

    async def cb_reg_s2_yes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            return
        meeting = await self.db.get_meeting(meeting_id)
        if meeting is None or meeting.canceled_at is not None:
            await cq.message.reply_text(
                format_unavailable(), reply_markup=build_unavailable_keyboard()
            )
            return
        await cq.message.reply_text(
            format_step3(), reply_markup=build_step3_keyboard(meeting_id)
        )

    async def cb_reg_s2_no(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            return
        await self._reply_registration_cancelled(cq.message, meeting_id)

    async def cb_reg_s3_yes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        db_user = await self.db.get_or_create_user(user.id, user.full_name, user.username)
        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            return
        meeting = await self.db.get_meeting(meeting_id)
        now_utc = datetime.now(tz.UTC)
        if meeting is None or meeting.canceled_at is not None:
            await cq.message.reply_text(
                format_unavailable(), reply_markup=build_unavailable_keyboard()
            )
            return
        if not await self.db.is_meeting_open(meeting_id, now_utc):
            await cq.message.reply_text(
                format_unavailable(), reply_markup=build_unavailable_keyboard()
            )
            return
        if await self._reply_overlap_confirm(
            cq.message,
            meeting,
            user.id,
            now_utc,
            keyboard_builder=lambda: build_overlap_confirm_keyboard(meeting_id),
        ):
            return
        await self._complete_registration(cq.message, meeting_id, user.id, db_user)

    async def cb_reg_overlap_yes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        db_user = await self.db.get_or_create_user(user.id, user.full_name, user.username)
        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            return
        result = await preflight_registration(
            self.db, meeting_id, user.id, self.local_tz
        )
        if result.error:
            await self._reply_preflight_error(cq.message, meeting_id, result.error)
            return
        now_utc = datetime.now(tz.UTC)
        if not await self.db.is_meeting_open(meeting_id, now_utc):
            await cq.message.reply_text(
                format_unavailable(), reply_markup=build_unavailable_keyboard()
            )
            return
        await self._complete_registration(cq.message, meeting_id, user.id, db_user)

    async def cb_reg_overlap_no(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            return
        await cq.message.reply_text(
            format_overlap_declined(),
            reply_markup=build_overlap_decline_keyboard(meeting_id),
        )

    async def cb_reg_s3_no(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            return
        await self._reply_registration_cancelled(cq.message, meeting_id)

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

        date_str = format_meeting_time(m, self.local_tz, style="details_date")
        time_str = format_meeting_time(m, self.local_tz, style="details_time")

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
            f"📅 {date_str}\n"
            f"🕐 {time_str}\n"
            f"📍 {m.location or 'TBA'}\n"
            f"👤 Ведет: {host_display}\n"
            f"👥 Идет: {confirmed} / {m.max_participants} участников (+ведущих: {hosts})"
        )
        user = update.effective_user
        calendar_keyboard = None
        if user:
            is_host = m.created_by == user.id
            is_participant = await self.db.is_registered(m.id, user.id)
            if can_offer_google_calendar(is_host=is_host, is_participant=is_participant):
                calendar_keyboard = self._google_calendar_keyboard(m, lang="ru")
                if calendar_keyboard:
                    details_text += f"\n\n{gcal_disclaimer('ru')}"
            entry = await self.db.get_active_waitlist_entry(m.id, user.id)
            if entry:
                position = await self.db.get_queue_position(entry.id) if entry.status == WaitlistStatus.WAITING else None
                waitlist_line = self.waitlist.format_details_waitlist_line(entry, position)
                if waitlist_line:
                    details_text += f"\n{waitlist_line}"
        await _reply_with_card(cq.message, details_text, m.photo_file_id, reply_markup=calendar_keyboard)

    async def cb_participants(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the host a numbered list of confirmed participants for their meeting."""
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
            await cq.message.reply_text("Список участников доступен только организатору встречи.")
            return

        rows = await self.db.list_confirmed_participants(meeting_id)
        text = self._format_participant_list(meeting, rows, meeting.max_participants)
        await cq.message.reply_text(text, parse_mode="HTML")

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

        when = format_meeting_time(meeting, self.local_tz, style="short")
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
            f"📅 {when}\n"
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

        if self.scheduler._bot:
            await notify_participants(
                self.scheduler._bot,
                self.db,
                canceled_meeting,
                ["встреча отменена"],
                exclude_user_id=user.id,
                local_tz=self.local_tz,
                bot_username=self.bot_username,
            )

        when = format_meeting_time(canceled_meeting, self.local_tz, style="short")
        text = (
            f"🚫 <b>Встреча отменена</b>\n\n"
            f"📌 {canceled_meeting.topic}\n"
            f"📅 {when}\n\n"
            "Встреча успешно отменена."
            f"{gcal_update_reminder('ru')}"
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

    async def cb_leave_meeting(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle 'Отменить участие' button - start cancellation reason flow."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()

        user = update.effective_user
        if not user:
            return

        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            await cq.message.reply_text("Ошибка: неверный запрос.")
            return

        self._clear_leave_pending(context)
        result = await preflight_leave(self.db, meeting_id, user.id)
        if result.error:
            await self._reply_leave_preflight_error(cq.message, result.error)
            return

        await cq.message.reply_text(
            format_reason_prompt(result.meeting, self.local_tz),
            reply_markup=build_reason_keyboard(meeting_id),
        )

    async def cb_leave_reason(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle cancellation reason selection."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()

        user = update.effective_user
        if not user:
            return

        parsed = parse_leave_reason_callback(cq.data or "")
        if parsed is None:
            await cq.message.reply_text("Ошибка: неверный запрос.")
            return
        reason_type, meeting_id = parsed

        self._clear_leave_pending(context)
        result = await preflight_leave(self.db, meeting_id, user.id)
        if result.error:
            await self._reply_leave_preflight_error(cq.message, result.error)
            return

        if reason_type == CancellationReasonType.OTHER:
            context.user_data[LEAVE_OTHER_PENDING_KEY] = {"meeting_id": meeting_id}
            await cq.message.reply_text(
                format_other_reason_prompt(),
                reply_markup=build_other_reason_keyboard(meeting_id),
            )
            return

        await self._show_final_leave_confirm(cq.message, meeting_id, reason_type)

    async def cb_leave_other_abort(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Abort free-text cancellation reason entry."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()

        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            return

        self._clear_leave_pending(context)
        await cq.message.reply_text(
            format_stayed_registered(),
            reply_markup=build_stayed_keyboard(meeting_id),
        )

    async def cb_leave_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle confirmed participation cancellation."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()

        user = update.effective_user
        if not user:
            return

        parsed = parse_leave_confirm_callback(cq.data or "")
        if parsed is None:
            await cq.message.reply_text("Ошибка: неверный запрос.")
            return
        reason_type, meeting_id = parsed

        result = await preflight_leave(self.db, meeting_id, user.id)
        if result.error:
            self._clear_leave_pending(context)
            await self._reply_leave_preflight_error(cq.message, result.error)
            return

        reason_text = None
        if reason_type == CancellationReasonType.OTHER:
            reason_text = context.user_data.get(LEAVE_OTHER_TEXT_KEY)
            if not reason_text:
                await cq.message.reply_text(
                    "Не удалось найти причину отмены. Пожалуйста, начни снова со встречи.",
                    reply_markup=build_stayed_keyboard(meeting_id),
                )
                return

        await self._complete_leave(
            cq.message,
            meeting_id,
            user.id,
            context,
            reason_type=reason_type,
            reason_text=reason_text,
            edit=True,
        )

    async def cb_leave_abort(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle abort of participation cancellation at final confirmation."""
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()

        meeting_id = self._parse_callback_meeting_id(cq.data or "")
        if meeting_id is None:
            await cq.message.reply_text("Ошибка: неверный запрос.")
            return

        self._clear_leave_pending(context)
        await cq.edit_message_text(
            format_stayed_registered(),
            reply_markup=build_stayed_keyboard(meeting_id),
        )

    async def cb_waitlist_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        meeting_id = int((cq.data or "").split(":", 1)[1])
        now_utc = datetime.now(tz.UTC)
        result = await self.waitlist.join_waitlist(meeting_id, user.id, now_utc)
        if not result.ok:
            await cq.message.reply_text(result.message)
            return
        meeting = await self.db.get_meeting(meeting_id)
        if not meeting:
            await cq.message.reply_text(result.message)
            return
        text = self.waitlist.format_join_confirmation(meeting, result.position)
        await cq.message.reply_text(
            text,
            reply_markup=self._build_waitlist_join_keyboard(meeting_id),
            parse_mode="HTML",
        )

    async def cb_waitlist_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        meeting_id = int((cq.data or "").split(":", 1)[1])
        result = await self.waitlist.cancel_waitlist(meeting_id, user.id)
        await cq.message.reply_text(result.message)

    async def cb_waitlist_view(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        meeting_id = int((cq.data or "").split(":", 1)[1])
        meeting = await self.db.get_meeting(meeting_id)
        if not meeting:
            await cq.message.reply_text("Встреча не найдена.")
            return
        if meeting.created_by != user.id:
            await cq.message.reply_text("Waitlist доступен только организатору встречи.")
            return
        rows = await self.db.list_waitlist_for_meeting(meeting_id, include_all_statuses=True)
        text = self.waitlist.format_host_waitlist(meeting, rows)
        await cq.message.reply_text(text, parse_mode="HTML")

    async def cb_offer_accept(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        await self.db.get_or_create_user(user.id, user.full_name, user.username)
        try:
            entry_id = int((cq.data or "").split(":", 1)[1])
        except (IndexError, ValueError):
            await cq.message.reply_text("Invalid offer request.")
            return
        entry = await self.db.get_waitlist_entry(entry_id)
        if entry is None or entry.user_id != user.id:
            await cq.message.reply_text("Предложение не найдено.")
            return
        if entry.status != WaitlistStatus.OFFERED:
            await cq.message.reply_text("Предложение больше недействительно.")
            return
        await cq.message.reply_text(
            format_offer_short_confirm(),
            reply_markup=build_offer_confirm_keyboard(entry_id),
        )

    async def cb_offer_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        db_user = await self.db.get_or_create_user(user.id, user.full_name, user.username)
        try:
            entry_id = int((cq.data or "").split(":", 1)[1])
        except (IndexError, ValueError):
            await cq.message.reply_text("Invalid offer request.")
            return
        entry = await self.db.get_waitlist_entry(entry_id)
        if entry is None or entry.user_id != user.id:
            await cq.message.reply_text("Предложение не найдено.")
            return
        meeting = await self.db.get_meeting(entry.meeting_id)
        if meeting is None or meeting.canceled_at is not None:
            await cq.message.reply_text(
                format_unavailable(), reply_markup=build_unavailable_keyboard()
            )
            return
        now_utc = datetime.now(tz.UTC)
        if await self._reply_overlap_confirm(
            cq.message,
            meeting,
            user.id,
            now_utc,
            keyboard_builder=lambda: build_offer_overlap_confirm_keyboard(entry_id),
        ):
            return
        await self._complete_offer_accept(cq.message, entry_id, user.id, db_user)

    async def cb_offer_overlap_yes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        db_user = await self.db.get_or_create_user(user.id, user.full_name, user.username)
        try:
            entry_id = int((cq.data or "").split(":", 1)[1])
        except (IndexError, ValueError):
            await cq.message.reply_text("Invalid offer request.")
            return
        entry = await self.db.get_waitlist_entry(entry_id)
        if entry is None or entry.user_id != user.id:
            await cq.message.reply_text("Предложение не найдено.")
            return
        meeting = await self.db.get_meeting(entry.meeting_id)
        if meeting is None or meeting.canceled_at is not None:
            await cq.message.reply_text(
                format_unavailable(), reply_markup=build_unavailable_keyboard()
            )
            return
        await self._complete_offer_accept(cq.message, entry_id, user.id, db_user)

    async def cb_offer_overlap_no(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        try:
            entry_id = int((cq.data or "").split(":", 1)[1])
        except (IndexError, ValueError):
            await cq.message.reply_text("Invalid offer request.")
            return
        entry = await self.db.get_waitlist_entry(entry_id)
        meeting_id = entry.meeting_id if entry else 0
        if meeting_id:
            await cq.message.reply_text(
                format_overlap_declined(),
                reply_markup=build_overlap_decline_keyboard(meeting_id),
            )
        else:
            await cq.message.reply_text(format_overlap_declined())

    async def cb_offer_decline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        cq = update.callback_query
        if not cq:
            return
        await cq.answer()
        user = update.effective_user
        if not user:
            return
        entry_id = int((cq.data or "").split(":", 1)[1])
        now_utc = datetime.now(tz.UTC)
        result = await self.waitlist.decline_offer(entry_id, user.id, now_utc)
        await cq.message.reply_text(result.message)
        await self._send_waitlist_offer_dms(result.notifications)
