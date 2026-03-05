from __future__ import annotations

from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from sqlalchemy import select, func

from app.core.config import settings
from app.core.plans import get_plan_limits
from app.db.database import AsyncSessionLocal
from app.db.models.tenant import Tenant
from app.db.repositories.tenant_repository import TenantRepository
from master_bot.notify import notify_tenant_owner

router = Router()

# ?? ??????: ?????? ??? ????????? ??????? ??????????????????????????????????????

def admin_only(func):
    """????????? ? ?????????? ?????? MASTER_ADMIN_TG_ID."""
    import functools
    @functools.wraps(func)
    async def wrapper(event, **kwargs):
        user_id = (
            event.from_user.id
            if hasattr(event, "from_user")
            else None
        )
        if user_id != settings.master_admin_tg_id:
            if hasattr(event, "answer"):
                await event.answer("? ??? ???????")
            return
        return await func(event, **kwargs)
    return wrapper


# ?? ??????????????? ??????? ????????????????????????????????????????????????????

def _plan_label(plan: str) -> str:
    return {"trial": "???????", "base": "???????", "pro": "Pro"}.get(plan, plan)


def _tenant_admin_text(t: Tenant) -> str:
    now = datetime.now(timezone.utc)
    status = "? ???????" if t.is_active else "?? ?????????"
    until = "?"
    days_left = ""
    if t.subscription_until:
        until = t.subscription_until.strftime("%d.%m.%Y")
        d = (t.subscription_until - now).days
        days_left = f" ({d} ??.)" if d >= 0 else " (???????)"
    onboarding = "?" if t.onboarding_completed else "?? ??????? /setup"
    return (
        f"?? <b>{t.company_name}</b>
"
        f"?? tenant_id: {t.id}
"
        f"?? owner_tg_id: {t.owner_tg_id}
"
        f"?? ??????: {status}
"
        f"?? ?????: {_plan_label(t.plan)}
"
        f"? ??: {until}{days_left}
"
        f"?? ?????????: {onboarding}
"
        f"?? ????? ? ?????: {t.leads_this_month} / "
        f"{'?' if t.max_leads_per_month == -1 else t.max_leads_per_month}
"
        f"?? API ????: {'????' if t.api_key else '???'}
"
    )


def _tenant_admin_keyboard(t: Tenant) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    if t.is_active:
        b.row(InlineKeyboardButton(
            text="?? ?????????????",
            callback_data=f"adm:block:{t.id}"
        ))
        b.row(InlineKeyboardButton(
            text="?? ???????? +30 ????",
            callback_data=f"adm:extend:{t.id}"
        ))
    else:
        b.row(InlineKeyboardButton(
            text="? ??????????????",
            callback_data=f"adm:unblock:{t.id}"
        ))
        if not t.trial_used:
            b.row(InlineKeyboardButton(
                text=f"?? ???? ??????? {settings.trial_days} ????",
                callback_data=f"adm:trial:{t.id}"
            ))
        b.row(InlineKeyboardButton(
            text="?? ???????????? +30 ????",
            callback_data=f"adm:extend:{t.id}"
        ))
    b.row(InlineKeyboardButton(
        text="?? ???????? ???????",
        callback_data=f"adm:msg:{t.id}"
    ))
    b.row(InlineKeyboardButton(
        text="?? ? ??????",
        callback_data="adm:clients:0"
    ))
    return b


# ?? /clients ? ?????? ???????? ?????????????????????????????????????????????????

PAGE_SIZE = 8

@router.message(Command("clients"))
@admin_only
async def cmd_clients(message: Message):
    await _show_clients_page(message, page=0, edit=False)


@router.callback_query(F.data.startswith("adm:clients:"))
async def cb_clients_page(callback: CallbackQuery):
    if callback.from_user.id != settings.master_admin_tg_id:
        await callback.answer("? ??? ???????", show_alert=True)
        return
    page = int(callback.data.split(":")[2])
    await callback.answer()
    await _show_clients_page(callback.message, page=page, edit=True)


async def _show_clients_page(message, page: int, edit: bool):
    async with AsyncSessionLocal() as session:
        total_result = await session.execute(
            select(func.count(Tenant.id))
        )
        total = total_result.scalar_one()

        result = await session.execute(
            select(Tenant)
            .order_by(Tenant.created_at.desc())
            .offset(page * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )
        tenants = list(result.scalars().all())

    now = datetime.now(timezone.utc)
    text = f"?? <b>???????</b> (?????: {total})

"
    builder = InlineKeyboardBuilder()

    for t in tenants:
        icon = "?" if t.is_active else "??"
        until = ""
        if t.subscription_until:
            d = (t.subscription_until - now).days
            until = f" {d}?."
        builder.row(InlineKeyboardButton(
            text=f"{icon} {t.company_name}{until}",
            callback_data=f"adm:detail:{t.id}"
        ))

    # ?????????
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            text="??", callback_data=f"adm:clients:{page-1}"
        ))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(
            text="??", callback_data=f"adm:clients:{page+1}"
        ))
    if nav:
        builder.row(*nav)

    builder.row(InlineKeyboardButton(
        text="?? ??????????", callback_data="adm:stats"
    ))

    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ?? /stats ? ??????? ?????????? ????????????????????????????????????????????????

@router.message(Command("stats"))
@admin_only
async def cmd_stats(message: Message):
    await _show_stats(message, edit=False)


@router.callback_query(F.data == "adm:stats")
async def cb_stats(callback: CallbackQuery):
    if callback.from_user.id != settings.master_admin_tg_id:
        await callback.answer("? ??? ???????", show_alert=True)
        return
    await callback.answer()
    await _show_stats(callback.message, edit=True)


async def _show_stats(message, edit: bool):
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
                Tenant.is_active == True, Tenant.plan == "trial"
            )
        )).scalar_one()

        paid = (await session.execute(
            select(func.count(Tenant.id)).where(
                Tenant.is_active == True, Tenant.plan != "trial"
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

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="?? ?????? ????????", callback_data="adm:clients:0"
    ))

    text = (
        f"?? <b>?????????? TelegramCRM</b>

"
        f"?? ????? ????????????????: {total}
"
        f"? ????????: {active}
"
        f"   ? ?? ???????: {trial}
"
        f"   ? ?? ???????: {paid}
"
        f"?? ???????? ????? 3 ???: {expiring}
"
        f"?? ?? ????????? /setup: {not_setup}
"
    )

    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


# ?? ???????? ??????? ???????????????????????????????????????????????????????????

@router.callback_query(F.data.startswith("adm:detail:"))
async def cb_adm_detail(callback: CallbackQuery):
    if callback.from_user.id != settings.master_admin_tg_id:
        await callback.answer("? ??? ???????", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
    if not tenant:
        await callback.answer("?? ???????", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


# ?? ?????????? ???????? ????????????????????????????????????????????????????????

@router.callback_query(F.data.startswith("adm:block:"))
async def cb_adm_block(callback: CallbackQuery):
    if callback.from_user.id != settings.master_admin_tg_id:
        await callback.answer("?", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        from sqlalchemy import update
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(is_active=False)
        )
        await session.commit()
        await session.refresh(tenant)
    await callback.answer("?? ????????????", show_alert=True)
    await notify_tenant_owner(
        tenant.owner_tg_id,
        "?? <b>??? ??????? ???????????? ???????????????.</b>

"
        f"?? ???????? ??????????: {settings.support_username}"
    )
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("adm:unblock:"))
async def cb_adm_unblock(callback: CallbackQuery):
    if callback.from_user.id != settings.master_admin_tg_id:
        await callback.answer("?", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        from sqlalchemy import update
        await session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(is_active=True)
        )
        await session.commit()
        await session.refresh(tenant)
    await callback.answer("? ?????????????", show_alert=True)
    await notify_tenant_owner(
        tenant.owner_tg_id,
        "? <b>??? ??????? ????????????.</b>

"
        "?????? ? CRM ???????????."
    )
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("adm:trial:"))
async def cb_adm_trial(callback: CallbackQuery):
    if callback.from_user.id != settings.master_admin_tg_id:
        await callback.answer("?", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if tenant.trial_used:
            await callback.answer("??????? ?????? ??? ???????????", show_alert=True)
            return
        api_key = await repo.activate_trial(tenant_id, days=settings.trial_days)
        await session.commit()
        await session.refresh(tenant)
    await callback.answer(f"? ??????? ?????? ?????", show_alert=True)
    await notify_tenant_owner(
        tenant.owner_tg_id,
        f"?? <b>????????????? ??????????? ??????? ??????!</b>

"
        f"???????? ??????? ?? "
        f"{tenant.subscription_until.strftime('%d.%m.%Y')}.
"
        f"???????? /start ??? ????????? ???????."
    )
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("adm:extend:"))
async def cb_adm_extend(callback: CallbackQuery):
    if callback.from_user.id != settings.master_admin_tg_id:
        await callback.answer("?", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        new_until, api_key = await repo.activate_subscription(
            tenant_id, days=30
        )
        await session.commit()
        tenant = await repo.get_by_id(tenant_id)
    await callback.answer(
        f"? ???????? ?? {new_until.strftime('%d.%m.%Y')}", show_alert=True
    )
    await notify_tenant_owner(
        tenant.owner_tg_id,
        f"?? <b>????????????? ??????? ????????!</b>

"
        f"??????? ?? {new_until.strftime('%d.%m.%Y')}."
    )
    await callback.message.edit_text(
        _tenant_admin_text(tenant),
        reply_markup=_tenant_admin_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


# ?? ???????? ??????? (??????? ????????) ???????????????????????????????????????

# ????????? ????????? ???????? (in-memory, ??????? ??? 1 ??????)
_pending_msg: dict[int, int] = {}  # admin_tg_id ? tenant_id

@router.callback_query(F.data.startswith("adm:msg:"))
async def cb_adm_msg(callback: CallbackQuery):
    if callback.from_user.id != settings.master_admin_tg_id:
        await callback.answer("?", show_alert=True)
        return
    tenant_id = int(callback.data.split(":")[2])
    _pending_msg[callback.from_user.id] = tenant_id
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="? ??????", callback_data=f"adm:detail:{tenant_id}"
    ))
    await callback.message.edit_text(
        "?? ??????? ????????? ??? ???????.
"
        "??? ????? ?????????? ??? ? ???? ???:",
        reply_markup=builder.as_markup(),
    )


@router.message(F.chat.type == "private")
async def handle_admin_message(message: Message):
    """????????????? ????? ?? ?????? ???? ?? ? ?????? ???????? ?????????."""
    if message.from_user.id != settings.master_admin_tg_id:
        return
    if message.from_user.id not in _pending_msg:
        return
    tenant_id = _pending_msg.pop(message.from_user.id)

    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)

    if not tenant:
        await message.answer("?????? ?? ??????.")
        return

    await notify_tenant_owner(
        tenant.owner_tg_id,
        f"?? <b>????????? ?? ??????????????:</b>

{message.text}"
    )
    await message.answer(
        f"? ????????? ?????????? ??????? {tenant.company_name}."
    )
