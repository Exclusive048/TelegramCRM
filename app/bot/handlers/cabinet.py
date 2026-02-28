"""
/cabinet — кабинет администрирования.
Доступен только CRM-администраторам.
"""
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, BufferedInputFile, FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.config import settings
from app.bot.constants.ttl import TTL_MENU_SEC, TTL_ERROR_SEC
from app.core.permissions import is_crm_admin
from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.db.models.lead import LeadStatus
from app.telegram.html_utils import html_escape
from app.telegram.safe_sender import TelegramSafeSender

router = Router()


async def _check_admin(sender: TelegramSafeSender, user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        return await is_crm_admin(sender, repo, settings.crm_group_id, user_id)


def _main_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📤 Экспорт клиентов", callback_data="cab:export"),
        InlineKeyboardButton(text="📊 Аналитика", callback_data="cab:analytics"),
    )
    builder.row(
        InlineKeyboardButton(text="🔗 Интеграции", callback_data="cab:integrations"),
        InlineKeyboardButton(text="💳 Тариф", callback_data="cab:tariff"),
    )
    return builder


def _stage_keyboard(prefix: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Лиды", callback_data=f"{prefix}:new"),
        InlineKeyboardButton(text="В работе", callback_data=f"{prefix}:in_progress"),
    )
    builder.row(
        InlineKeyboardButton(text="Оплачено", callback_data=f"{prefix}:paid"),
        InlineKeyboardButton(text="Успех", callback_data=f"{prefix}:success"),
    )
    builder.row(
        InlineKeyboardButton(text="Отклонено", callback_data=f"{prefix}:rejected"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="cab:back"))
    return builder


def _period_keyboard(prefix: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Сегодня", callback_data=f"{prefix}:today"),
        InlineKeyboardButton(text="Неделя", callback_data=f"{prefix}:week"),
        InlineKeyboardButton(text="Месяц", callback_data=f"{prefix}:month"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="cab:back"))
    return builder


def _period_dates(period: str) -> tuple[datetime, datetime]:
    now = datetime.now()
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now
    if period == "week":
        return now - timedelta(days=7), now
    return now - timedelta(days=30), now


@router.message(Command("cabinet"))
async def cmd_cabinet(message: Message, sender: TelegramSafeSender):
    if not await _check_admin(sender, message.from_user.id):
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Кабинет доступен только CRM-администраторам.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    await sender.send_ephemeral_text(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="Кабинет\n\nВыберите раздел:",
        reply_markup=_main_keyboard().as_markup(),
        ttl_sec=TTL_MENU_SEC,
    )
    try:
        await sender.delete_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            thread_id=message.message_thread_id,
        )
    except Exception:
        pass


@router.callback_query(F.data == "cab:back")
async def cab_back(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback)
        return
    await sender.answer(callback)
    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text="Кабинет\n\nВыберите раздел:",
        reply_markup=_main_keyboard().as_markup(),
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )


# ── Экспорт ──────────────────────────────────────────


@router.callback_query(F.data == "cab:export")
async def cab_export_menu(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text="Экспорт клиентов\nВыберите этап:",
        reply_markup=_stage_keyboard("cab:export_stage").as_markup(),
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )


@router.callback_query(F.data.startswith("cab:export_stage:"))
async def cab_export_period(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    stage = callback.data.split(":")[2]
    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text="📅 <b>Выберите период</b>:",
        reply_markup=_period_keyboard(f"cab:export_do:{stage}").as_markup(),
        parse_mode="HTML",
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )


@router.callback_query(F.data.startswith("cab:export_do:"))
async def cab_export_do(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) < 4:
        await sender.answer(callback)
        return
    stage = parts[2]
    period = parts[3]
    date_from, date_to = _period_dates(period)

    status = LeadStatus(stage)

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        leads, total = await repo.get_list(status=status, date_from=date_from, date_to=date_to, per_page=10000)

    if not leads:
        msg = await sender.answer(callback.message, "Нет заявок по выбранному фильтру.")
        await sender.schedule_delete(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            thread_id=msg.message_thread_id,
            ttl_sec=TTL_MENU_SEC,
        )
        return

    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Заявки"

    headers = [
        "ID", "Имя", "Телефон", "Почта", "Источник", "Услуга", "Сумма",
        "Статус", "Ответственный", "Комментарий", "UTM", "Дата создания", "Дата закрытия",
    ]
    header_fill = PatternFill("solid", fgColor="1A56DB")
    header_font = Font(bold=True, color="FFFFFF")

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    status_labels = {
        "new": "Лиды",
        "in_progress": "В работе",
        "paid": "Оплачено",
        "success": "Успех",
        "rejected": "Отклонено",
    }

    for row, lead in enumerate(leads, 2):
        ws.cell(row=row, column=1, value=lead.id)
        ws.cell(row=row, column=2, value=lead.name)
        ws.cell(row=row, column=3, value=lead.phone)
        ws.cell(row=row, column=4, value=lead.email or "")
        ws.cell(row=row, column=5, value=lead.source)
        ws.cell(row=row, column=6, value=lead.service or "")
        ws.cell(row=row, column=7, value=float(lead.amount) if lead.amount is not None else "")
        ws.cell(row=row, column=8, value=status_labels.get(lead.status.value, lead.status.value))
        ws.cell(row=row, column=9, value=lead.manager.name if lead.manager else "")
        ws.cell(row=row, column=10, value=lead.comment or "")
        ws.cell(row=row, column=11, value=lead.utm_campaign or "")
        ws.cell(row=row, column=12, value=lead.created_at.strftime("%d.%m.%Y %H:%M") if lead.created_at else "")
        ws.cell(row=row, column=13, value=lead.closed_at.strftime("%d.%m.%Y %H:%M") if lead.closed_at else "")

        if row % 2 == 0:
            fill = PatternFill("solid", fgColor="F0F7FF")
            for col in range(1, len(headers) + 1):
                ws.cell(row=row, column=col).fill = fill

    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"leads_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    await sender.send_document(
        chat_id=callback.message.chat.id,
        message_thread_id=callback.message.message_thread_id,
        document=BufferedInputFile(buffer.read(), filename=filename),
        caption=f"📤 Экспорт: {total} заявок\nФайл: {filename}",
        parse_mode=None,
        ttl_sec=TTL_MENU_SEC,
    )


# ── Аналитика ────────────────────────────────────────


@router.callback_query(F.data == "cab:analytics")
async def cab_analytics(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📈 Конверсия", callback_data="cab:analytics:conversion"),
        InlineKeyboardButton(text="👥 Работа с заявками", callback_data="cab:analytics:activity"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="cab:back"))
    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text="📊 <b>Аналитика</b>\nВыберите режим:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )


@router.callback_query(F.data.startswith("cab:analytics:conversion"))
async def cab_analytics_conversion(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text="📈 <b>Конверсия</b>\nВыберите период:",
        reply_markup=_period_keyboard("cab:analytics_conversion").as_markup(),
        parse_mode="HTML",
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )


@router.callback_query(F.data.startswith("cab:analytics_conversion:"))
async def cab_analytics_conversion_period(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    period = callback.data.split(":")[2]
    date_from, date_to = _period_dates(period)

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        stats = await repo.get_conversion_stats(date_from=date_from, date_to=date_to)

    s = stats["by_status"]
    period_line = f"За период с {date_from:%d.%m.%y} - {date_to:%d.%m.%y}"
    total = stats["total"]

    lines = [
        "📈 <b>Конверсия</b>",
        period_line,
        f"Всего лидов: {total}",
        f"Взято в работу: {s.get('in_progress', 0)} ({_pct(s.get('in_progress', 0), total)})",
        f"Оплачено: {s.get('paid', 0)} ({_pct(s.get('paid', 0), total)})",
        f"Успех: {s.get('success', 0)} ({_pct(s.get('success', 0), total)})",
        f"Отклонено: {s.get('rejected', 0)} ({_pct(s.get('rejected', 0), total)})",
    ]

    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text="\n".join(lines),
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )


@router.callback_query(F.data.startswith("cab:analytics:activity"))
async def cab_analytics_activity(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text="👥 <b>Работа с заявками</b>\nВыберите период:",
        reply_markup=_period_keyboard("cab:analytics_activity").as_markup(),
        parse_mode="HTML",
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )


@router.callback_query(F.data.startswith("cab:analytics_activity:"))
async def cab_analytics_activity_period(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    period = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        managers = await repo.get_all_managers()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="All", callback_data=f"cab:analytics_activity_run:{period}:all"))
    for m in managers:
        builder.row(InlineKeyboardButton(text=m.name, callback_data=f"cab:analytics_activity_run:{period}:{m.id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="cab:back"))
    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text="Выберите сотрудника:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )


@router.callback_query(F.data.startswith("cab:analytics_activity_run:"))
async def cab_analytics_activity_run(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    _, _, period, manager_raw = callback.data.split(":")
    date_from, date_to = _period_dates(period)
    manager_id = None if manager_raw == "all" else int(manager_raw)

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        stats = await repo.get_activity_stats(date_from=date_from, date_to=date_to, manager_id=manager_id)

    total = stats["total"]
    s = stats["by_status"]
    period_line = f"За период с {date_from:%d.%m.%y} - {date_to:%d.%m.%y}"
    lines = [
        "👥 <b>Работа с заявками</b>",
        period_line,
        f"Всего лидов: {total}",
        f"Взято в работу: {s.get('in_progress', 0)} ({_pct(s.get('in_progress', 0), total)})",
        f"Оплачено: {s.get('paid', 0)} ({_pct(s.get('paid', 0), total)})",
        f"Успех: {s.get('success', 0)} ({_pct(s.get('success', 0), total)})",
        f"Отклонено: {s.get('rejected', 0)} ({_pct(s.get('rejected', 0), total)})",
    ]

    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text="\n".join(lines),
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )


def _pct(part: int, total: int) -> str:
    if not total:
        return "0%"
    return f"{round(part / total * 100)}%"


# ── Интеграции ───────────────────────────────────────


@router.callback_query(F.data == "cab:integrations")
async def cab_integrations(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    raw_domain = (settings.public_domain or "YOUR_DOMAIN").strip()
    domain = raw_domain.replace("https://", "").replace("http://", "").strip("/") or "YOUR_DOMAIN"
    url = f"https://{domain}/api/v1/leads/tilda"
    escaped_url = html_escape(url)
    escaped_key = html_escape(settings.api_secret_key)
    text = (
        "🔗 <b>Webhook для Tilda</b>\n"
        f"POST {escaped_url}\n"
        f"X-API-Key: {escaped_key}\n"
        "📋 JS-сниппет отправлен файлом"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📋 Скопировать URL", callback_data="cab:copy_webhook"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="cab:back"))
    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text=text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )
    try:
        snippet = FSInputFile("integrations/tilda_snippet.js")
        await sender.send_document(
            chat_id=callback.message.chat.id,
            message_thread_id=callback.message.message_thread_id,
            document=snippet,
            caption="JS-сниппет для Tilda",
            parse_mode=None,
            ttl_sec=TTL_MENU_SEC,
        )
    except Exception:
        await sender.send_ephemeral_text(
            chat_id=callback.message.chat.id,
            message_thread_id=callback.message.message_thread_id,
            text="Не удалось отправить файл JS-сниппета. Проверьте integrations/tilda_snippet.js",
            ttl_sec=TTL_ERROR_SEC,
        )


@router.callback_query(F.data == "cab:copy_webhook")
async def cab_copy_webhook(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback)
        return
    await sender.answer(callback)
    raw_domain = (settings.public_domain or "YOUR_DOMAIN").strip()
    domain = raw_domain.replace("https://", "").replace("http://", "").strip("/") or "YOUR_DOMAIN"
    url = f"https://{domain}/api/v1/leads/tilda"
    await sender.send_ephemeral_text(
        chat_id=callback.message.chat.id,
        message_thread_id=callback.message.message_thread_id,
        text=url,
        ttl_sec=TTL_MENU_SEC,
    )


# ── Тариф ────────────────────────────────────────────


@router.callback_query(F.data == "cab:tariff")
async def cab_tariff(callback: CallbackQuery, sender: TelegramSafeSender):
    if not await _check_admin(sender, callback.from_user.id):
        await sender.answer(callback, "⛔️ Нет доступа", show_alert=True)
        return
    await sender.answer(callback)
    await sender.edit_text(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        text='💳 Текущий тариф: Базовый\nЗаявок в месяц: без ограничений\nПоддержка: @support_username',
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="cab:back")
        ).as_markup(),
        thread_id=callback.message.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )
