"""Telegram keyboard and calendar UI components.

This module contains reusable UI components for the bot:
- MeetingCalendar: Customized calendar widget with Russian translations
- MonthPickerKeyboard: Custom 3-month selection keyboard
- Time picker keyboards: Hour and minute selection interfaces
"""
from __future__ import annotations

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram_bot_calendar import DetailedTelegramCalendar


# Russian translations for calendar navigation steps
LSTEP_TRANSLATIONS = {
    "y": "год",
    "m": "месяц", 
    "d": "день",
}

# Russian month names
RUSSIAN_MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]

# Time picker configuration
HOUR_START = 6    # First selectable hour
HOUR_END = 23     # Last selectable hour
HOURS_PER_ROW = 4  # Number of hour buttons per row
MINUTE_OPTIONS = (0, 15, 30, 45)  # Available minute increments


class MeetingCalendar(DetailedTelegramCalendar):
    """Calendar widget customized for meeting date selection.
    
    Extends DetailedTelegramCalendar with custom navigation buttons
    and cleaner empty state display. Starts at day selection (skips year/month).
    """
    first_step = "d"  # Start directly at day selection
    prev_button = "⬅️"
    next_button = "➡️"
    empty_month_button = ""
    empty_year_button = ""


class MonthPickerKeyboard:
    """Factory for month picker inline keyboard.
    
    Shows the next 3 months starting from tomorrow as buttons,
    formatted as "Month Name Year".
    """
    
    @staticmethod
    def build() -> InlineKeyboardMarkup:
        """Build an inline keyboard with the next 3 months.
        
        Returns:
            InlineKeyboardMarkup with month selection buttons.
        """
        tomorrow = date.today() + timedelta(days=1)
        buttons = []
        
        for i in range(3):
            month_date = tomorrow + relativedelta(months=i)
            month_name = RUSSIAN_MONTHS[month_date.month - 1]
            label = f"{month_name} {month_date.year}"
            callback_data = f"month:{month_date.year}:{month_date.month}"
            buttons.append([
                InlineKeyboardButton(text=label, callback_data=callback_data)
            ])
        
        return InlineKeyboardMarkup(buttons)
    
    @staticmethod
    def parse_callback(data: str) -> tuple[int, int] | None:
        """Parse month selection callback data.
        
        Args:
            data: Callback data string in format "month:YYYY:MM"
            
        Returns:
            Tuple of (year, month) or None if invalid format.
        """
        if not data.startswith("month:"):
            return None
        parts = data.split(":")
        if len(parts) != 3:
            return None
        try:
            return int(parts[1]), int(parts[2])
        except ValueError:
            return None


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

