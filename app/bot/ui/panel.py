from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models.lead import Lead, LeadStatus, Manager
from app.bot.utils.card import format_lead_card
from app.bot.keyboards.lead_keyboards import make_lead_keyboard
from app.telegram.html_utils import html_escape


def render_panel_home() -> str:
    return (
        "🧭 <b>Пульт управления</b>\n\n"
        "Выберите раздел:"
    )


def render_panel_team(managers: list[Manager]) -> str:
    lines: list[str] = ["👥 <b>Команда</b>\n"]
    if not managers:
        lines.append("Пока нет менеджеров.")
        return "\n".join(lines)

    for manager in managers:
        role_icon = "👑" if manager.is_admin else "👤"
        status_icon = "✅" if manager.is_active else "🚫"
        username = f"@{manager.tg_username}" if manager.tg_username else "—"
        lines.append(
            f"{role_icon} <b>{html_escape(manager.name)}</b> {status_icon}  {html_escape(username)}"
        )
    return "\n".join(lines)


def render_panel_team_add_prompt() -> str:
    return (
        "👥 <b>Добавить менеджера</b>\n\n"
        "Пришлите контакт менеджера (поделитесь контактом)."
    )


def render_lead_card(lead: Lead, manager_name: str | None = None) -> str:
    return format_lead_card(lead)


def _render_lead_status_line(lead: Lead, manager_name: str | None) -> str:
    if lead.status == LeadStatus.NEW:
        return "🔵 Статус: Новый"
    if lead.status == LeadStatus.IN_PROGRESS:
        mgr = manager_name or (lead.manager.name if lead.manager else None) or "—"
        return f"🟡 В работе: {html_escape(mgr)}"
    if lead.status == LeadStatus.SUCCESS:
        return "🟢 Закрыто"
    if lead.status == LeadStatus.REJECTED:
        return "🔴 Отклонено"
    return f"Статус: {html_escape(str(lead.status))}"


def build_kb_panel_home() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👥 Команда", callback_data="panel:team"),
    )
    return builder.as_markup()


def build_kb_panel_team(managers: list[Manager]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for manager in managers:
        role_icon = "👑" if manager.is_admin else "👤"
        status_icon = "✅" if manager.is_active else "🚫"
        label = f"{role_icon} {manager.name} {status_icon}"
        builder.row(
            InlineKeyboardButton(text=label, callback_data="panel:team"),
        )
    builder.row(InlineKeyboardButton(text="➕ Добавить", callback_data="team:add"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="panel:home"))
    return builder.as_markup()


def build_kb_panel_team_add_prompt() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✖️ Отмена", callback_data="team:cancel"))
    return builder.as_markup()


def build_kb_lead_actions(lead: Lead) -> InlineKeyboardMarkup | None:
    return make_lead_keyboard(lead.id, lead.status)


def parse_panel_callback(data: str) -> str | None:
    if data == "panel:home":
        return "panel_home"
    if data == "panel:team":
        return "panel_team"
    if data == "team:add":
        return "team_add"
    if data == "team:cancel":
        return "team_cancel"
    return None


def parse_lead_callback(data: str) -> tuple[str, int] | None:
    if not data.startswith("lead:"):
        return None
    _, _, rest = data.partition(":")
    action, sep, lead_id_raw = rest.partition(":")
    if sep != ":":
        return None
    if action not in {"take", "paid", "success", "reject", "clone", "note", "remind", "back"}:
        return None
    if not lead_id_raw.isdigit():
        return None
    return action, int(lead_id_raw)
