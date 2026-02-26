"""
/cabinet — кабинет выгрузок и управления.
Доступен только CRM-администраторам.
"""
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from loguru import logger

from app.core.config import settings
from app.core.permissions import is_crm_admin
from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.db.models.lead import LeadStatus

router = Router()


async def _check_admin(bot: Bot, user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        return await is_crm_admin(bot, repo, settings.crm_group_id, user_id)


def _cabinet_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 Статистика",     callback_data="cab:stats"),
        InlineKeyboardButton(text="📥 Выгрузка Excel", callback_data="cab:export_menu"),
    )
    builder.row(
        InlineKeyboardButton(text="📧 Отправить на email", callback_data="cab:email_menu"),
        InlineKeyboardButton(text="👥 Менеджеры",          callback_data="cab:managers"),
    )
    return builder


# ── /cabinet ──────────────────────────────────────────

@router.message(Command("cabinet"))
async def cmd_cabinet(message: Message, bot: Bot):
    if not await _check_admin(bot, message.from_user.id):
        await message.answer("⛔️ Кабинет доступен только CRM-администраторам.")
        return

    builder = _cabinet_keyboard()
    await message.answer(
        "🗂 <b>Кабинет управления</b>\n\nВыберите действие:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ── Статистика ────────────────────────────────────────

@router.callback_query(F.data == "cab:stats")
async def cab_stats(callback: CallbackQuery, bot: Bot):
    if not await _check_admin(bot, callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await callback.answer()

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Сегодня",   callback_data="stats:today"),
        InlineKeyboardButton(text="Неделя",    callback_data="stats:week"),
        InlineKeyboardButton(text="Месяц",     callback_data="stats:month"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="cab:back"))
    await callback.message.edit_text(
        "📊 <b>Статистика</b>\nВыберите период:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("stats:"))
async def cab_stats_period(callback: CallbackQuery, bot: Bot):
    if not await _check_admin(bot, callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await callback.answer()

    period = callback.data.split(":")[1]
    now = datetime.utcnow()
    date_from = {
        "today": now.replace(hour=0, minute=0, second=0),
        "week":  now - timedelta(days=7),
        "month": now - timedelta(days=30),
    }.get(period)

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        stats = await repo.get_stats(date_from=date_from)

    period_label = {"today": "Сегодня", "week": "7 дней", "month": "30 дней"}[period]
    s = stats["by_status"]

    lines = [
        f"📊 <b>Статистика — {period_label}</b>",
        "",
        f"📋 Всего заявок:      <b>{stats['total']}</b>",
        f"📥 Новые:             <b>{s.get('new', 0)}</b>",
        f"🔄 В обработке:       <b>{s.get('in_progress', 0)}</b>",
        f"✅ Закрытые:          <b>{s.get('closed', 0)}</b>",
        f"❌ Отклонённые:       <b>{s.get('rejected', 0)}</b>",
        f"\n🎯 Конверсия:         <b>{stats['conversion']}%</b>",
    ]

    if stats["by_source"]:
        lines.append("\n<b>По источникам:</b>")
        for src, cnt in sorted(stats["by_source"].items(), key=lambda x: -x[1]):
            lines.append(f"  • {src}: {cnt}")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Сегодня", callback_data="stats:today"),
        InlineKeyboardButton(text="Неделя",  callback_data="stats:week"),
        InlineKeyboardButton(text="Месяц",   callback_data="stats:month"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="cab:back"))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ── Меню выгрузки ─────────────────────────────────────

@router.callback_query(F.data == "cab:export_menu")
async def cab_export_menu(callback: CallbackQuery, bot: Bot):
    if not await _check_admin(bot, callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await callback.answer()

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📥 Все заявки",       callback_data="export:all:all"),
        InlineKeyboardButton(text="✅ Закрытые",          callback_data="export:closed:all"),
    )
    builder.row(
        InlineKeyboardButton(text="🔄 В обработке",      callback_data="export:in_progress:all"),
        InlineKeyboardButton(text="❌ Отклонённые",      callback_data="export:rejected:all"),
    )
    builder.row(
        InlineKeyboardButton(text="📅 За сегодня",       callback_data="export:all:today"),
        InlineKeyboardButton(text="📅 За неделю",        callback_data="export:all:week"),
        InlineKeyboardButton(text="📅 За месяц",         callback_data="export:all:month"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="cab:back"))

    await callback.message.edit_text(
        "📥 <b>Выгрузка в Excel</b>\n\nВыберите что выгружать:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("export:"))
async def cab_do_export(callback: CallbackQuery, bot: Bot):
    if not await _check_admin(bot, callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await callback.answer("⏳ Формирую файл...")

    _, status_str, period = callback.data.split(":")
    status = None if status_str == "all" else LeadStatus(status_str)

    now = datetime.utcnow()
    date_from = {"today": now.replace(hour=0, minute=0, second=0),
                 "week":  now - timedelta(days=7),
                 "month": now - timedelta(days=30),
                 "all":   None}.get(period)

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        leads, total = await repo.get_list(status=status, date_from=date_from, per_page=10000)

    if not leads:
        await callback.message.answer("📭 Нет заявок по выбранным фильтрам.")
        return

    # Генерируем Excel
    import io
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Заявки"

    headers = ["ID", "Имя", "Телефон", "Источник", "Услуга", "Комментарий", "Статус", "Менеджер", "UTM", "Дата"]
    header_fill = PatternFill("solid", fgColor="1A56DB")
    header_font = Font(bold=True, color="FFFFFF")

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    status_labels = {"new": "Новая", "in_progress": "В обработке", "closed": "Закрыта", "rejected": "Отклонена"}

    for row, lead in enumerate(leads, 2):
        ws.cell(row=row, column=1,  value=lead.id)
        ws.cell(row=row, column=2,  value=lead.name)
        ws.cell(row=row, column=3,  value=lead.phone)
        ws.cell(row=row, column=4,  value=lead.source)
        ws.cell(row=row, column=5,  value=lead.service or "")
        ws.cell(row=row, column=6,  value=lead.comment or "")
        ws.cell(row=row, column=7,  value=status_labels.get(lead.status.value, lead.status.value))
        ws.cell(row=row, column=8,  value=lead.manager.name if lead.manager else "")
        ws.cell(row=row, column=9,  value=lead.utm_campaign or "")
        ws.cell(row=row, column=10, value=lead.created_at.strftime("%d.%m.%Y %H:%M") if lead.created_at else "")

        if row % 2 == 0:
            fill = PatternFill("solid", fgColor="F0F7FF")
            for col in range(1, 11):
                ws.cell(row=row, column=col).fill = fill

    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    from aiogram.types import BufferedInputFile
    filename = f"leads_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    await callback.message.answer_document(
        BufferedInputFile(buffer.read(), filename=filename),
        caption=f"📊 Выгрузка: {total} заявок\nФайл: {filename}",
    )


# ── Email выгрузка ────────────────────────────────────

class EmailState(StatesGroup):
    waiting_for_email = State()


@router.callback_query(F.data == "cab:email_menu")
async def cab_email_menu(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await _check_admin(bot, callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await state.set_state(EmailState.waiting_for_email)
    default = settings.default_export_email
    hint = f"\n\nТекущий email: <code>{default}</code>" if default else ""
    await callback.message.edit_text(
        f"📧 <b>Отправить выгрузку на email</b>{hint}\n\nВведите email адрес:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="◀️ Отмена", callback_data="cab:back")
        ).as_markup(),
    )


@router.message(EmailState.waiting_for_email)
async def handle_email_input(message: Message, state: FSMContext):
    await state.clear()
    email = message.text.strip()
    if "@" not in email:
        await message.answer("⚠️ Некорректный email. Попробуйте снова через /cabinet")
        return
    await message.answer(
        f"✅ Email <code>{email}</code> сохранён.\n"
        f"Функция отправки на email будет доступна после настройки SMTP в .env\n\n"
        f"<i>Пока используйте выгрузку прямо в чат — файл будет здесь.</i>",
        parse_mode="HTML",
    )


# ── Список менеджеров ─────────────────────────────────

@router.callback_query(F.data == "cab:managers")
async def cab_managers(callback: CallbackQuery, bot: Bot):
    if not await _check_admin(bot, callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await callback.answer()

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        managers = await repo.get_all_managers()

    if not managers:
        text = "👥 Менеджеров пока нет."
    else:
        lines = ["<b>👥 Команда:</b>\n"]
        for m in managers:
            icon = "👑" if m.is_admin else "👤"
            role = "Админ" if m.is_admin else "Менеджер"
            username = f"@{m.tg_username}" if m.tg_username else "—"
            lines.append(f"{icon} {m.name} ({role})  {username}")
        text = "\n".join(lines)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="cab:back"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ── Назад ─────────────────────────────────────────────

@router.callback_query(F.data == "cab:back")
async def cab_back(callback: CallbackQuery, bot: Bot):
    if not await _check_admin(bot, callback.from_user.id):
        await callback.answer()
        return
    await callback.answer()
    builder = _cabinet_keyboard()
    await callback.message.edit_text(
        "🗂 <b>Кабинет управления</b>\n\nВыберите действие:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
