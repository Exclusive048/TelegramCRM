from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.db.models.lead import LeadStatus


def make_lead_keyboard(lead_id: int, status: LeadStatus) -> InlineKeyboardMarkup | None:
    """
    Кнопки под карточкой зависят от статуса.
    CLOSED и REJECTED — кнопок нет (карточка архивная).
    """
    builder = InlineKeyboardBuilder()

    if status == LeadStatus.NEW:
        builder.row(
            InlineKeyboardButton(text="🔄 Взять в обработку", callback_data=f"lead:take:{lead_id}"),
            InlineKeyboardButton(text="❌ Отклонить",          callback_data=f"lead:reject:{lead_id}"),
        )

    elif status == LeadStatus.IN_PROGRESS:
        builder.row(
            InlineKeyboardButton(text="✅ Закрыть сделку", callback_data=f"lead:close:{lead_id}"),
            InlineKeyboardButton(text="❌ Отклонить",       callback_data=f"lead:reject:{lead_id}"),
        )
        builder.row(
            InlineKeyboardButton(text="💬 Добавить заметку", callback_data=f"lead:comment:{lead_id}"),
        )

    else:
        # CLOSED / REJECTED — никаких кнопок
        return None

    return builder.as_markup()
