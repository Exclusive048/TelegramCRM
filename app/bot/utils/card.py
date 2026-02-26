from app.db.models.lead import Lead


def format_lead_card(lead: Lead, show_comments: bool = True) -> str:
    """
    Форматирует заявку в текст для Telegram (HTML).
    show_comments=True — показывает все заметки менеджеров.
    """
    lines = [
        f"<b>📋 Заявка #{lead.id}</b>",
        "",
        f"👤 <b>Имя:</b> {lead.name}",
        f"📱 <b>Телефон:</b> {lead.phone}",
    ]

    if lead.service:
        lines.append(f"🎯 <b>Услуга:</b> {lead.service}")

    source_label = {
        "tg_bot":   "Telegram Bot",
        "website":  "Сайт",
        "landing":  "Лендинг",
        "tilda":    "Tilda",
        "manual":   "Ручной ввод",
    }.get(lead.source, lead.source)
    lines.append(f"🔗 <b>Источник:</b> {source_label}")

    if lead.utm_campaign:
        lines.append(f"📣 <b>Кампания:</b> {lead.utm_campaign}")

    if lead.comment:
        lines.append(f"💬 <b>Комментарий:</b> {lead.comment}")

    # Дополнительные поля
    if lead.extra:
        extra_labels = {"city": "🌆 Город", "budget": "💰 Бюджет", "email": "📧 Email"}
        for key, value in lead.extra.items():
            label = extra_labels.get(key, f"  {key.capitalize()}")
            lines.append(f"{label}: {value}")

    if lead.created_at:
        lines.append(f"\n🕐 {lead.created_at.strftime('%d.%m.%Y  %H:%M')}")

    # ── Заметки менеджеров (фикс: теперь показываются) ────
    if show_comments and lead.comments:
        lines.append("\n<b>📝 Заметки:</b>")
        for c in lead.comments:
            ts = c.created_at.strftime('%d.%m %H:%M') if c.created_at else ""
            lines.append(f"  • <i>{c.author}</i> [{ts}]: {c.text}")

    return "\n".join(lines)
