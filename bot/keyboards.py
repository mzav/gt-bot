"""Telegram keyboard and calendar UI components.

This module contains reusable UI components for the bot:
- MeetingCalendar: Customized calendar widget with Russian translations
- Time picker keyboards: Hour and minute selection interfaces
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram_bot_calendar import DetailedTelegramCalendar


# Russian translations for calendar navigation steps
LSTEP_TRANSLATIONS = {
    "y": "год",
    "m": "месяц", 
    "d": "день",
}

# Time picker configuration
HOUR_START = 6    # First selectable hour
HOUR_END = 23     # Last selectable hour
HOURS_PER_ROW = 4  # Number of hour buttons per row
MINUTE_OPTIONS = (0, 15, 30, 45)  # Available minute increments


class MeetingCalendar(DetailedTelegramCalendar):
    """Calendar widget customized for meeting date selection.
    
    Extends DetailedTelegramCalendar with custom navigation buttons
    and cleaner empty state display.
    """
    prev_button = "⬅️"
    next_button = "➡️"
    empty_month_button = ""
    empty_year_button = ""


class TimePickerKeyboard:
    """Factory for time picker inline keyboards.
    
    Provides methods to build hour and minute selection keyboards
    with consistent styling and back-navigation support.
    """
    
    @staticmethod
    def build_hours() -> InlineKeyboardMarkup:
        """Build an inline keyboard with hour buttons.
        
        Creates a grid of hour buttons from HOUR_START to HOUR_END,
        arranged in rows of HOURS_PER_ROW buttons each.
        
        Returns:
            InlineKeyboardMarkup with hour selection buttons.
        """
        keyboard = []
        row = []
        
        for hour in range(HOUR_START, HOUR_END + 1):
            button = InlineKeyboardButton(
                text=f"{hour:02d}:00",
                callback_data=f"hour:{hour}"
            )
            row.append(button)
            
            if len(row) == HOURS_PER_ROW:
                keyboard.append(row)
                row = []
        
        # Add remaining buttons if any
        if row:
            keyboard.append(row)
            
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def build_minutes(hour: int) -> InlineKeyboardMarkup:
        """Build an inline keyboard with minute options for a selected hour.
        
        Args:
            hour: The previously selected hour to display in button labels.
            
        Returns:
            InlineKeyboardMarkup with minute buttons and back navigation.
        """
        minute_buttons = [
            InlineKeyboardButton(
                text=f"{hour:02d}:{minute:02d}",
                callback_data=f"time:{hour}:{minute}"
            )
            for minute in MINUTE_OPTIONS
        ]
        
        back_button = InlineKeyboardButton(
            text="⬅️ Назад к выбору часа",
            callback_data="hour:back"
        )
        
        return InlineKeyboardMarkup([
            minute_buttons,
            [back_button],
        ])

