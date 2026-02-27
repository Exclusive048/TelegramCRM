from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models.lead import LeadStatus


def make_lead_keyboard(lead_id: int, status: LeadStatus) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="🔔 Установить напоминание", callback_data=f"lead:remind:{lead_id}"),
    )

    if status == LeadStatus.NEW:
        builder.row(
            InlineKeyboardButton(text="✅ Взять в работу", callback_data=f"lead:take:{lead_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"lead:reject:{lead_id}"),
        )
    elif status == LeadStatus.IN_PROGRESS:
        builder.row(
            InlineKeyboardButton(text="💳 Оплачено", callback_data=f"lead:paid:{lead_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"lead:reject:{lead_id}"),
        )
    elif status == LeadStatus.PAID:
        builder.row(
            InlineKeyboardButton(text="🏆 Завершить", callback_data=f"lead:success:{lead_id}"),
            InlineKeyboardButton(text="↩️ Отменить", callback_data=f"lead:reject:{lead_id}"),
        )
    elif status in (LeadStatus.SUCCESS, LeadStatus.REJECTED):
        builder.row(
            InlineKeyboardButton(text="📋 Создать копию", callback_data=f"lead:clone:{lead_id}"),
        )

    builder.row(
        InlineKeyboardButton(text="📝 Заметка", callback_data=f"lead:note:{lead_id}"),
    )

    return builder.as_markup()


def make_reject_reason_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Нет бюджета", callback_data=f"lead:reject_reason:{lead_id}:no_budget"),
        InlineKeyboardButton(text="Не дозвонились", callback_data=f"lead:reject_reason:{lead_id}:no_answer"),
    )
    builder.row(
        InlineKeyboardButton(text="Не целевой", callback_data=f"lead:reject_reason:{lead_id}:not_target"),
        InlineKeyboardButton(text="Передумал", callback_data=f"lead:reject_reason:{lead_id}:changed_mind"),
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Своя причина", callback_data=f"lead:reject_reason:{lead_id}:custom"),
    )
    return builder.as_markup()


def make_reminder_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Через 1 час", callback_data=f"lead:remind_set:{lead_id}:1h"),
        InlineKeyboardButton(text="Через 3 часа", callback_data=f"lead:remind_set:{lead_id}:3h"),
    )
    builder.row(
        InlineKeyboardButton(text="Завтра", callback_data=f"lead:remind_set:{lead_id}:tomorrow"),
        InlineKeyboardButton(text="✏️ Своё время", callback_data=f"lead:remind_set:{lead_id}:custom"),
    )
    return builder.as_markup()
