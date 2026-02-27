from __future__ import annotations

import html
from datetime import datetime

from app.db.models.lead import Lead, LeadStatus


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


def format_lead_card(lead: Lead) -> str:
    """
    Формат карточки для Telegram (HTML).
    """
    name = html.escape(lead.name or "—")
    phone = html.escape(lead.phone or "—")
    email = html.escape(lead.email or "—")
    source_label = {
        "tg_bot": "Telegram Bot",
        "website": "Сайт",
        "landing": "Лендинг",
        "tilda": "Tilda",
        "manual": "Ручной ввод",
    }.get(lead.source, lead.source or "—")
    source_label = html.escape(source_label)

    created_at = _fmt_date(lead.created_at, "%d.%m.%y")
    comment = html.escape(lead.comment or "—")

    lines = [
        f"📋 Заявка #{lead.id}",
        f"👤 Имя: {name}",
        f"📱 Телефон: {phone}",
        f"📧 Почта: {email}",
        f"🔗 Источник: {source_label}",
        f"📅 Дата заявки: {created_at}",
    ]

    if lead.manager:
        lines.append(f"👨‍💼 Ответственный: {html.escape(lead.manager.name)}")

    if lead.amount is not None:
        lines.append(f"💰 Сумма: {lead.amount} руб.")

    if lead.status in (LeadStatus.SUCCESS, LeadStatus.REJECTED):
        lines.append(f"📅 Дата закрытия: {_fmt_date(lead.closed_at, '%d.%m.%y')}")

    if lead.status == LeadStatus.REJECTED:
        reason = html.escape(lead.reject_reason or "—")
        lines.append(f"❌ Причина: {reason}")

    lines.append(f"💬 Комментарий: {comment}")

    if lead.comments:
        lines.append("📝 Заметки:")
        for c in lead.comments:
            ts = _fmt_date(c.created_at, "%d.%m %H:%M")
            author = html.escape(c.author or "—")
            text = html.escape(c.text or "")
            lines.append(f"• {author} [{ts}]: {text}")

    return "\n".join(lines)


def format_archive_card(lead: Lead) -> str:
    base = format_lead_card(lead)
    return f"{base}\n\U0001f4cc \u0421\u0442\u0430\u0442\u0443\u0441: {_status_label(lead.status)}"
