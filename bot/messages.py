"""User-facing message constants."""

from .main_menu import MENU_CREATE, MENU_HELP, MENU_MEETINGS, MENU_MY

FLOW_DONE_MESSAGE = "Готово"

RESTRICTED_ACCESS_MESSAGE = (
    "Привет 🌿\n\n"
    "Этот бот доступен только участницам закрытого сообщества.\n\n"
    "Если ты уже состоишь в канале, но всё равно видишь это сообщение, "
    "пожалуйста, напиши администратору."
)

REGISTRATION_SUCCESS = (
    "Готово, ты записана на встречу ✨\n\n"
    "Пожалуйста, отнесись к участию бережно: хост заранее готовится, "
    "планирует место и вкладывает своё время и силы. "
    "Если планы изменятся, отмени участие в боте как можно раньше - "
    "так место сможет занять кто-то другой.\n\n"
    "Спасибо, что помогаешь делать встречи комфортными для всех 💛"
)

WELCOME_MESSAGE = (
    "🌸 Добро пожаловать в GirlTalkBot! 🌸\n\n"
    "Я помогаю комьюнити Girl Talk Berlin создавать встречи и записываться на них.\n\n"
    "Выбери действие в меню ниже 👇\n\n"
    f"{MENU_MEETINGS} — все предстоящие встречи, можно записаться\n"
    f"{MENU_MY} — встречи, где ты хост или участница\n"
    f"{MENU_CREATE} — организовать новую встречу\n"
    f"{MENU_HELP} — как пользоваться ботом\n\n"
    "В любой момент можно отменить создание или редактирование встречи командой /cancel "
    "или кнопкой «Отмена».\n\n"
    "👉 Подпишись на <a href='https://t.me/+8D9imfRpZDAxMDdi'>канал с анонсами</a> — "
    "там выходят новые встречи и ссылки для записи 👈\n\n"
    "Оставить <a href='https://forms.gle/vVEt78wAvj38RrwQ7'>обратную связь</a> ✅\n\n"
    "Давай делать организацию встреч проще вместе! ✨"
)

CALENDAR_SUBSCRIPTION_HELP = (
    "🗓 Как добавить расписание встреч в Google Calendar одной постоянной ссылкой:\n\n"
    "1. Открой Google Calendar на компьютере\n"
    "2. В разделе Other calendars нажми “+”\n"
    "3. Выбери “From URL”\n"
    "4. Вставь эту ссылку:\n"
    "    {calendar_feed_url}\n\n"
    "Готово — встречи будут появляться в отдельном календаре. "
    "Важно: Google Calendar может обновлять внешний календарь не сразу, "
    "поэтому самая актуальная информация всегда остаётся в боте."
)


def build_welcome_message(*, calendar_feed_url: str | None = None) -> str:
    """Build the /start and /help welcome text, optionally with calendar subscription info."""
    if not calendar_feed_url:
        return WELCOME_MESSAGE
    return f"{WELCOME_MESSAGE}\n\n{CALENDAR_SUBSCRIPTION_HELP.format(calendar_feed_url=calendar_feed_url)}"
