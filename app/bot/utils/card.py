from __future__ import annotations

from datetime import datetime

from app.db.models.lead import Lead, LeadStatus
from app.telegram.html_utils import html_escape


def _fmt_date(value: datetime | None, fmt: str) -> str:
    if not value:
        return "—"
    return value.strftime(fmt)


def _status_label(status: LeadStatus) -> str:
    labels = {
        LeadStatus.NEW: "Лиды",
        LeadStatus.IN_PROGRESS: "В работе",
        LeadStatus.PAID: "Оплачено",
        LeadStatus.SUCCESS: "Успех",
        LeadStatus.REJECTED: "Отклонено",
    }
    return labels.get(status, status.value)


def format_lead_card(lead: Lead, reminder_at: datetime | None = None) -> str:
    """
    Формат карточки для Telegram (HTML).
    """
    name = html_escape(lead.name or "—")
    phone = html_escape(lead.phone or "—")
    email = html_escape(lead.email or "—")
    source_label = {
        "tg_bot": "Telegram Bot",
        "website": "Сайт",
        "landing": "Лендинг",
        "tilda": "Tilda",
        "manual": "Ручной ввод",
    }.get(lead.source, lead.source or "—")
    source_label = html_escape(source_label)

    created_at = _fmt_date(lead.created_at, "%d.%m.%y")
    comment = html_escape(lead.comment or "—")

    lines = [
        f"📋 Заявка #{lead.id}",
        f"👤 Имя: {name}",
        f"📱 Телефон: {phone}",
        f"📧 Почта: {email}",
        f"🔗 Источник: {source_label}",
        f"📅 Дата заявки: {created_at}",
    ]

    if lead.manager:
        lines.append(f"👨‍💼 Ответственный: {html_escape(lead.manager.name)}")

    if lead.amount is not None:
        lines.append(f"💰 Сумма: {lead.amount} руб.")

    if reminder_at is not None:
        lines.append(f"🔔 Напоминание: {reminder_at:%d.%m в %H:%M}")

    if lead.status in (LeadStatus.SUCCESS, LeadStatus.REJECTED):
        lines.append(f"📅 Дата закрытия: {_fmt_date(lead.closed_at, '%d.%m.%y')}")

    if lead.status == LeadStatus.REJECTED:
        reason = html_escape(lead.reject_reason or "—")
        lines.append(f"❌ Причина: {reason}")

    lines.append(f"💬 Комментарий: {comment}")

    if lead.comments:
        lines.append("📝 Заметки:")
        for comment_item in lead.comments:
            ts = _fmt_date(comment_item.created_at, "%d.%m %H:%M")
            author = html_escape(comment_item.author or "—")
            text = html_escape(comment_item.text or "")
            lines.append(f"• {author} [{ts}]: {text}")

    return "\n".join(lines)


def format_archive_card(lead: Lead) -> str:
    base = format_lead_card(lead)
    return f"{base}\n📌 Статус: {_status_label(lead.status)}"
