from __future__ import annotations

import functools
from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from sqlalchemy import select, func, update

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models.tenant import Tenant
from app.db.repositories.tenant_repository import TenantRepository
from master_bot.notify import notify_tenant_owner

router = Router()

PAGE_SIZE = 8

# Хранилище ожидающих отправки сообщения (in-memory)
_pending_msg: dict[int, int] = {}  # admin_tg_id → tenant_id


# ── Проверка что это админ ─────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id == settings.master_admin_tg_id


# ── Вспомогательные функции ────────────────────────────────────────────────────

def _plan_label(plan: str) -> str:
    return {"trial": "Пробный", "base": "Базовый", "pro": "Pro"}.get(plan, plan or "—")


def _tenant_admin_text(t: Tenant) -> str:
    now = datetime.now(timezone.utc)
    status = "✅ Активен" if t.is_active else "🔴 Неактивен"
    until = "—"
    days_left = ""
    if t.subscription_until:
        until = t.subscription_until.strftime("%d.%m.%Y")
        d = (t.subscription_until - now).days
        days_left = f" ({d} дн.)" if d >= 0 else " (истекла)"
    onboarding = "✅" if t.onboarding_completed else "⚠️ ожидает /setup"
    max_leads = "∞" if t.max_leads_per_month == -1 else str(t.max_leads_per_month)
    return (
        f"🏢 <b>{t.company_name}</b>\n"
        f"🆔 tenant_id: {t.id}\n"
        f"👤 owner_tg_id: {t.owner_tg_id}\n"
        f"📊 Статус: {status}\n"
        f"💰 Тариф: {_plan_label(t.plan)}\n"
        f"⏰ До: {until}{days_left}\n"
        f"🔧 Онбординг: {onboarding}\n"
        f"📨 Лидов в месяц: {t.leads_this_month} / {max_leads}\n"
        f"🔑 API ключ: {'есть' if t.api_key else 'нет'}\n"
    )


def _tenant_admin_keyboard(t: Tenant) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    if t.is_active:
        b.row(InlineKeyboardButton(
            text="🚫 Заблокировать",
            callback_data=f"adm:block:{t.id}",
        ))
        b.row(InlineKeyboardButton(
            text="📅 Продлить +30 дней",
            callback_data=f"adm:extend:{t.id}",
        ))
    else:
        b.row(InlineKeyboardButton(
            text="✅ Разблокировать",
            callback_data=f"adm:unblock:{t.id}",
        ))
        if not t.trial_used:
            b.row(InlineKeyboardButton(
                text=f"🆓 Дать пробный {settings.trial_days} дней",
                callback_data=f"adm:trial:{t.id}",
            ))
        b.row(InlineKeyboardButton(
            text="📅 Активировать +30 дней",
            callback_data=f"adm:extend:{t.id}",
        ))
    b.row(InlineKeyboardButton(
        text="✉️ Написать клиенту",
        callback_data=f"adm:msg:{t.id}",
    ))
    b.row(InlineKeyboardButton(
        text="⬅️ К списку",
        callback_data="adm:clients:0",
    ))
    return b


# ── /clients — список клиентов ─────────────────────────────────────────────────

@router.message(Command("clients"))
async def cmd_clients(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа")
        return
    await _show_clients_page(message, page=0, edit=False)


@router.callback_query(F.data.startswith("adm:clients:"))
async def cb_clients_page(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    page = int(callback.data.split(":")[2])
    await callback.answer()
    await _show_clients_page(callback.message, page=page, edit=True)


async def _show_clients_page(message: Message, page: int, edit: bool) -> None:
    async with AsyncSessionLocal() as session:
        total_result = await session.execute(select(func.count(Tenant.id)))
        total = total_result.scalar_one()

        result = await session.execute(
            select(Tenant)
            .order_by(Tenant.created_at.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        tenants = list(result.scalars().all())

    now = datetime.now(timezone.utc)
    text = f"👥 <b>Клиенты</b> (всего: {total})\n\n"
    builder = InlineKeyboardBuilder()

    for t in tenants:
        icon = "✅" if t.is_active else "🔴"
        until = ""
        if t.subscription_until:
            d = (t.subscription_until - now).days
            until = f" {d}д."
        builder.row(InlineKeyboardButton(
            text=f"{icon} {t.company_name}{until}",
            callback_data=f"adm:detail:{t.id}",
        ))

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="◀️", callback_data=f"adm:clients:{page - 1}"
        ))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="▶️", callback_data=f"adm:clients:{page + 1}"
        ))
    if nav:
        builder.row(*nav)

    builder.row(InlineKeyboardButton(
        text="📊 Статистика", callback_data="adm:stats"
    ))

    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ── /stats — сводная статистика ────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа")
        return
    await _show_stats(message, edit=False)


@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _show_stats(callback.message, edit=True)


async def _show_stats(message: Message, edit: bool) -> None:
    now = datetime.now(timezone.utc)
    warn_date = now + timedelta(days=3)

    async with AsyncSessionLocal() as session:
        total = (await session.execute(
            select(func.count(Tenant.id))
        )).scalar_one()

        active = (await session.execute(
            select(func.count(Tenant.id)).where(Tenant.is_active == True)
        )).scalar_one()

        trial = (await session.execute(
            select(func.count(Tenant.id)).where(
                Tenant.is_active == True,
                Tenant.plan == "trial",
            )
        )).scalar_one()

        paid = (await session.execute(
            select(func.count(Tenant.id)).where(
                Tenant.is_active == True,
                Tenant.plan != "trial",
            )
        )).scalar_one()

        expiring = (await session.execute(
            select(func.count(Tenant.id)).where(
                Tenant.is_active == True,
                Tenant.subscription_until != None,
                Tenant.subscription_until <= warn_date,
                Tenant.subscription_until >= now,
            )
        )).scalar_one()

        not_setup = (await session.execute(
            select(func.count(Tenant.id)).where(
                Tenant.is_active == True,
                Tenant.onboarding_completed == False,
            )
        )).scalar_one()

    text = (
        f"📊 <b>Статистика TelegramCRM</b>\n\n"
        f"👥 Всего зарегистрировано: {total}\n"
        f"✅ Активных: {active}\n"
        f"   └ 🆓 Пробный: {trial}\n"
        f"   └ 💳 Платных: {paid}\n"
        f"⚠️ Истекают через 3 дня: {expiring}\n"
        f"🔧 Не завершили /setup: {not_setup}\n"
    )

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="👥 Список клиентов", callback_data="adm:clients:0"
    ))

    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ── Карточка клиента ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:detail:"))
async def cb_adm_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
    if not tenant:
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


# ── Блокировка / разблокировка ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:block:"))
async def cb_adm_block(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(is_active=False)
        )
        await session.commit()
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
    await callback.answer("🚫 Заблокирован", show_alert=True)
    await notify_tenant_owner(
        tenant.owner_tg_id,
        f"🚫 <b>Ваш аккаунт заблокирован администратором.</b>\n\n"
        f"По вопросам обратитесь: {settings.support_username}",
    )
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("adm:unblock:"))
async def cb_adm_unblock(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(is_active=True)
        )
        await session.commit()
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
    await callback.answer("✅ Разблокирован", show_alert=True)
    await notify_tenant_owner(
        tenant.owner_tg_id,
        "✅ <b>Ваш аккаунт восстановлен.</b>\n\nДоступ к CRM активирован.",
    )
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


# ── Пробный период от админа ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:trial:"))
async def cb_adm_trial(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if tenant.trial_used:
            await callback.answer("Пробный период уже использован", show_alert=True)
            return
        await repo.activate_trial(tenant_id, days=settings.trial_days)
        await session.commit()
        tenant = await repo.get_by_id(tenant_id)
    await callback.answer("✅ Пробный период выдан", show_alert=True)
    await notify_tenant_owner(
        tenant.owner_tg_id,
        f"🎁 <b>Администратор активировал пробный период!</b>\n\n"
        f"Подписка активна до "
        f"{tenant.subscription_until.strftime('%d.%m.%Y')}.\n"
        f"Напишите /start для просмотра деталей.",
    )
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


# ── Продлить подписку от админа ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:extend:"))
async def cb_adm_extend(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        new_until, api_key = await repo.activate_subscription(tenant_id, days=30)
        await session.commit()
        tenant = await repo.get_by_id(tenant_id)
    await callback.answer(
        f"✅ Продлено до {new_until.strftime('%d.%m.%Y')}", show_alert=True
    )
    await notify_tenant_owner(
        tenant.owner_tg_id,
        f"🎁 <b>Администратор продлил подписку!</b>\n\n"
        f"Активна до {new_until.strftime('%d.%m.%Y')}.",
    )
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


# ── Написать клиенту ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("adm:msg:"))
async def cb_adm_msg(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    _pending_msg[callback.from_user.id] = tenant_id
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="❌ Отмена", callback_data=f"adm:detail:{tenant_id}"
    ))
    await callback.message.edit_text(
        "✉️ Введите сообщение для клиента.\nОно будет отправлено ему в личку:",
        reply_markup=builder.as_markup(),
    )


@router.message(F.chat.type == "private", ~F.text.startswith("/"))
async def handle_admin_freetext(message: Message):
    """Перехватывает текст от админа когда он в режиме отправки сообщения клиенту."""
    if not is_admin(message.from_user.id):
        return
    if message.from_user.id not in _pending_msg:
        return

    tenant_id = _pending_msg.pop(message.from_user.id)

    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)

    if not tenant:
        await message.answer("Тенант не найден.")
        return

    await notify_tenant_owner(
        tenant.owner_tg_id,
        f"✉️ <b>Сообщение от администратора:</b>\n\n{message.text}",
    )
    await message.answer(
        f"✅ Сообщение отправлено клиенту <b>{tenant.company_name}</b>.",
        parse_mode="HTML",
    )