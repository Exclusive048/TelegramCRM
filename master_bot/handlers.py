from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from app.core.config import settings
from app.bot.utils.callback_parser import safe_parse
from app.db.database import AsyncSessionLocal
from app.db.models.lead import ManagerRole
from app.db.models.tenant import Tenant
from app.db.repositories.lead_repository import LeadRepository
from app.db.repositories.tenant_repository import TenantRepository
from master_bot.notify import notify_admin

router = Router()


class RegState(StatesGroup):
    waiting_for_name = State()


# в”Ђв”Ђ Р’СЃРїРѕРјРѕРіР°С‚РµР»СЊРЅС‹Рµ С„СѓРЅРєС†РёРё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

def _status_line(tenant: Tenant) -> str:
    icon = "вњ…" if tenant.is_active else "рџ”ґ"
    until = ""
    if tenant.subscription_until:
        until = f" РґРѕ {tenant.subscription_until.strftime('%d.%m.%Y')}"
    plan_map = {"trial": "РџСЂРѕР±РЅС‹Р№", "base": "Р‘Р°Р·РѕРІС‹Р№", "pro": "РџСЂРѕ"}
    plan = plan_map.get(tenant.plan, tenant.plan or "вЂ”")
    return f"{icon} <b>{tenant.company_name}</b> вЂ” {plan}{until}"


def _tenant_detail_text(tenant: Tenant) -> str:
    now = datetime.now(timezone.utc)
    status = "вњ… РђРєС‚РёРІРЅР°" if tenant.is_active else "рџ”ґ РќРµР°РєС‚РёРІРЅР°"
    until = "вЂ”"
    days_left_str = ""
    if tenant.subscription_until:
        until = tenant.subscription_until.strftime("%d.%m.%Y")
        delta = (tenant.subscription_until - now).days
        days_left_str = f" (РѕСЃС‚Р°Р»РѕСЃСЊ {delta} РґРЅ.)" if delta >= 0 else " (РёСЃС‚РµРєР»Р°)"
    plan_map = {
        "trial": "РџСЂРѕР±РЅС‹Р№",
        "base": "Р‘Р°Р·РѕРІС‹Р№ 990 СЂСѓР±/РјРµСЃ",
        "pro": "РџСЂРѕ 2490 СЂСѓР±/РјРµСЃ",
    }
    plan = plan_map.get(tenant.plan, tenant.plan or "вЂ”")
    onboarding = "вњ… РќР°СЃС‚СЂРѕРµРЅР°" if tenant.onboarding_completed else "вљ пёЏ РћР¶РёРґР°РµС‚ /setup"
    return (
        f"рџЏў <b>{tenant.company_name}</b>\n"
        f"рџ“Љ РЎС‚Р°С‚СѓСЃ: {status}\n"
        f"рџ’° РўР°СЂРёС„: {plan}\n"
        f"вЏ° РџРѕРґРїРёСЃРєР° РґРѕ: {until}{days_left_str}\n"
        f"рџ”§ Р“СЂСѓРїРїР° CRM: {onboarding}\n"
        f"рџ”‘ Р РµС„. РєРѕРґ: <code>{tenant.referral_code or 'вЂ”'}</code>\n"
    )


def _account_keyboard(tenant: Tenant) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()

    if not tenant.trial_used:
        b.row(InlineKeyboardButton(
            text=f"рџ†“ РђРєС‚РёРІРёСЂРѕРІР°С‚СЊ РїСЂРѕР±РЅС‹Р№ {settings.trial_days} РґРЅ.",
            callback_data=f"reg:trial:{tenant.id}",
        ))

    # РљРЅРѕРїРєР° РѕРїР»Р°С‚С‹ вЂ” РїРѕРєР°Р·С‹РІР°С‚СЊ РІСЃРµРіРґР°
    label = "рџ’і РџСЂРѕРґР»РёС‚СЊ РїРѕРґРїРёСЃРєСѓ" if tenant.is_active else "рџ’і РћРїР»Р°С‚РёС‚СЊ РїРѕРґРїРёСЃРєСѓ"
    b.row(InlineKeyboardButton(
        text=label,
        callback_data=f"acc:pay:{tenant.id}",
    ))

    if tenant.api_key:
        b.row(InlineKeyboardButton(
            text="рџ”‘ РњРѕРё API РєР»СЋС‡Рё",
            callback_data=f"acc:keys:{tenant.id}",
        ))

    b.row(InlineKeyboardButton(
        text="рџ‘Ґ Р РµС„РµСЂР°Р»СЊРЅР°СЏ РїСЂРѕРіСЂР°РјРјР°",
        callback_data=f"acc:ref:{tenant.id}",
    ))
    b.row(InlineKeyboardButton(text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data="main:back"))
    return b


# в”Ђв”Ђ /start в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    logger.debug(f"[MASTER] cmd_start from={message.from_user.id} chat_type={message.chat.type}")
    if message.chat.type != "private":
        return

    referred_code: str | None = None
    if command.args and command.args.startswith("ref_"):
        referred_code = command.args[4:].upper()

    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenants = await repo.get_tenants_by_owner(message.from_user.id)

    if tenants:
        await state.clear()
        await _show_my_accounts(message, tenants)
        return

    await state.set_state(RegState.waiting_for_name)
    await state.update_data(referred_code=referred_code)

    ref_note = ""
    if referred_code:
        ref_note = "\n\nрџЋЃ Р’С‹ РїСЂРёС€Р»Рё РїРѕ СЂРµС„РµСЂР°Р»СЊРЅРѕР№ СЃСЃС‹Р»РєРµ вЂ” РїСЂРё РїРµСЂРІРѕР№ РѕРїР»Р°С‚Рµ РїРѕР»СѓС‡РёС‚Рµ Р±РѕРЅСѓСЃ!"

    await message.answer(
        "рџ‘‹ Р”РѕР±СЂРѕ РїРѕР¶Р°Р»РѕРІР°С‚СЊ РІ <b>TelegramCRM</b>!\n\n"
        "CRM-СЃРёСЃС‚РµРјР° РїСЂСЏРјРѕ РІ Telegram: Р·Р°СЏРІРєРё, РјРµРЅРµРґР¶РµСЂС‹, Р°РЅР°Р»РёС‚РёРєР°, РІРѕСЂРѕРЅРєР° РїСЂРѕРґР°Р¶."
        f"{ref_note}\n\n"
        "Р”Р»СЏ РЅР°С‡Р°Р»Р° РІРІРµРґРёС‚Рµ РЅР°Р·РІР°РЅРёРµ РІР°С€РµР№ РєРѕРјРїР°РЅРёРё РёР»Рё РїСЂРѕРµРєС‚Р°:",
        parse_mode="HTML",
    )


# в”Ђв”Ђ Р’РІРѕРґ РЅР°Р·РІР°РЅРёСЏ РєРѕРјРїР°РЅРёРё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.message(RegState.waiting_for_name)
async def handle_company_name(message: Message, state: FSMContext):
    logger.debug(f"[MASTER] handle_company_name CALLED from={message.from_user.id} text={message.text!r}")
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("вљ пёЏ РќР°Р·РІР°РЅРёРµ СЃР»РёС€РєРѕРј РєРѕСЂРѕС‚РєРѕРµ. РњРёРЅРёРјСѓРј 2 СЃРёРјРІРѕР»Р°.")
        return
    if len(name) > 100:
        await message.answer("вљ пёЏ РЎР»РёС€РєРѕРј РґР»РёРЅРЅРѕРµ РЅР°Р·РІР°РЅРёРµ. РњР°РєСЃРёРјСѓРј 100 СЃРёРјРІРѕР»РѕРІ.")
        return

    data = await state.get_data()
    referred_code: str | None = data.get("referred_code")
    await state.clear()

    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        referrer_id: int | None = None
        if referred_code:
            referrer = await repo.get_by_referral_code(referred_code)
            if referrer:
                referrer_id = referrer.id
        tenant = await repo.create_tenant(
            owner_tg_id=message.from_user.id,
            company_name=name,
            referred_by_id=referrer_id,
        )
        lead_repo = LeadRepository(session)
        await lead_repo.upsert_manager_from_contact(
            tg_id=message.from_user.id,
            name=message.from_user.full_name,
            username=message.from_user.username,
            role=ManagerRole.ADMIN,
            tenant_id=tenant.id,
        )
        tenants = await repo.get_tenants_by_owner(message.from_user.id)
        await session.commit()
        tenant_id = tenant.id

    await notify_admin(
        f"рџ†• РќРѕРІС‹Р№ РєР»РёРµРЅС‚!\n"
        f"рџЏў <b>{name}</b>\n"
        f"рџ‘¤ @{message.from_user.username or 'вЂ”'} (id:{message.from_user.id})\n"
        f"рџ†” tenant_id: {tenant_id}"
        + (f"\nрџ”— РџСЂРёС€С‘Р» РїРѕ СЂРµС„. РєРѕРґСѓ: {referred_code}" if referred_code else "")
    )

    await message.answer(
        f"вњ… РђРєРєР°СѓРЅС‚ <b>{name}</b> СЃРѕР·РґР°РЅ!\n\n"
        "РќРёР¶Рµ СЃРїРёСЃРѕРє РІР°С€РёС… CRM-Р°РєРєР°СѓРЅС‚РѕРІ. Р’С‹Р±РµСЂРёС‚Рµ РЅСѓР¶РЅС‹Р№ РґР»СЏ СѓРїСЂР°РІР»РµРЅРёСЏ.",
        parse_mode="HTML",
    )
    await _show_my_accounts(message, tenants)


# в”Ђв”Ђ РЎРїРёСЃРѕРє Р°РєРєР°СѓРЅС‚РѕРІ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

async def _show_my_accounts(message_or_callback, tenants: list[Tenant]) -> None:
    text = "рџ“‹ <b>Р’Р°С€Рё Р°РєРєР°СѓРЅС‚С‹ CRM:</b>\n\n"
    for t in tenants:
        text += _status_line(t) + "\n"

    builder = InlineKeyboardBuilder()
    for t in tenants:
        icon = "вњ…" if t.is_active else "рџ”ґ"
        builder.row(InlineKeyboardButton(
            text=f"{icon} {t.company_name}",
            callback_data=f"acc:detail:{t.id}",
        ))
    builder.row(InlineKeyboardButton(
        text="вћ• Р—Р°СЂРµРіРёСЃС‚СЂРёСЂРѕРІР°С‚СЊ РµС‰С‘",
        callback_data="main:new",
    ))

    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(
            text, reply_markup=builder.as_markup(), parse_mode="HTML"
        )
    else:
        await message_or_callback.message.edit_text(
            text, reply_markup=builder.as_markup(), parse_mode="HTML"
        )


@router.callback_query(F.data == "main:back")
async def cb_main_back(callback: CallbackQuery):
    await callback.answer()
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenants = await repo.get_tenants_by_owner(callback.from_user.id)
    await _show_my_accounts(callback, tenants)


@router.callback_query(F.data == "main:new")
async def cb_main_new(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(RegState.waiting_for_name)
    # answer() Р° РЅРµ edit_text() вЂ” РёРЅР°С‡Рµ FSM РЅРµ РїРѕРґС…РІР°С‚РёС‚ СЃР»РµРґСѓСЋС‰РµРµ СЃРѕРѕР±С‰РµРЅРёРµ
    await callback.message.answer("в„№пёЏ Р’РІРµРґРёС‚Рµ РЅР°Р·РІР°РЅРёРµ РЅРѕРІРѕРіРѕ Р°РєРєР°СѓРЅС‚Р° CRM.")


# в”Ђв”Ђ РљР°СЂС‚РѕС‡РєР° Р°РєРєР°СѓРЅС‚Р° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.callback_query(F.data.startswith("acc:detail:"))
async def cb_acc_detail(callback: CallbackQuery):
    parsed = safe_parse(callback.data, expected_parts=3, expected_types=(str, str, int))
    if not parsed:
        await callback.answer("⚠️ Некорректный callback.", show_alert=True)
        return
    _, _, tenant_id = parsed
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if not tenant or tenant.owner_tg_id != callback.from_user.id:
            await callback.answer("в›”пёЏ РќРµ РЅР°Р№РґРµРЅРѕ.", show_alert=True)
            return
        if not tenant.management_api_key:
            tenant.management_api_key = await repo.ensure_management_api_key(tenant_id)
            await session.commit()
            await session.refresh(tenant)
    await callback.answer()
    await callback.message.edit_text(
        _tenant_detail_text(tenant),
        reply_markup=_account_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


# в”Ђв”Ђ РџСЂРѕР±РЅС‹Р№ РїРµСЂРёРѕРґ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.callback_query(F.data.startswith("reg:trial:"))
async def cb_reg_trial(callback: CallbackQuery):
    parsed = safe_parse(callback.data, expected_parts=3, expected_types=(str, str, int))
    if not parsed:
        await callback.answer("⚠️ Некорректный callback.", show_alert=True)
        return
    _, _, tenant_id = parsed
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if not tenant or tenant.owner_tg_id != callback.from_user.id:
            await callback.answer("в›”пёЏ РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
            return
        if await repo.has_owner_used_trial(callback.from_user.id):
            await callback.answer(
                "⚠️ Пробный период уже использован на одном из ваших аккаунтов.",
                show_alert=True,
            )
            return
        if tenant.trial_used:
            await callback.answer("вљ пёЏ РџСЂРѕР±РЅС‹Р№ РїРµСЂРёРѕРґ СѓР¶Рµ РёСЃРїРѕР»СЊР·РѕРІР°РЅ.", show_alert=True)
            return
        api_key = await repo.activate_trial(tenant_id, days=settings.trial_days)
        await session.commit()
        await session.refresh(tenant)

    await callback.answer("вњ… РџСЂРѕР±РЅС‹Р№ РїРµСЂРёРѕРґ Р°РєС‚РёРІРёСЂРѕРІР°РЅ!")
    await notify_admin(
        f"рџ†“ РџСЂРѕР±РЅС‹Р№ РїРµСЂРёРѕРґ\nрџЏў <b>{tenant.company_name}</b> (ID:{tenant_id})"
    )
    await _send_activation_message(callback.message, tenant, api_key)


# в”Ђв”Ђ РћРїР»Р°С‚Р° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

async def _process_payment(callback: CallbackQuery) -> None:
    parsed = safe_parse(callback.data, expected_parts=3, expected_types=(str, str, int))
    if not parsed:
        await callback.answer("⚠️ Некорректный callback.", show_alert=True)
        return
    _, _, tenant_id = parsed

    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)

    if not tenant or tenant.owner_tg_id != callback.from_user.id:
        await callback.answer("в›”пёЏ РќРµС‚ РґРѕСЃС‚СѓРїР°.", show_alert=True)
        return

    await callback.answer()

    # Р®РљР°СЃСЃР° РЅРµ РЅР°СЃС‚СЂРѕРµРЅР° вЂ” РїРѕРєР°Р·Р°С‚СЊ Р·Р°РіР»СѓС€РєСѓ
    if not settings.yukassa_shop_id or not settings.yukassa_secret_key:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data=f"acc:detail:{tenant_id}"
        ))
        await callback.message.edit_text(
            f"рџ’і <b>РћРїР»Р°С‚Р° РїРѕРґРїРёСЃРєРё</b>\n\n"
            f"рџЏў {tenant.company_name}\n"
            f"рџ’° РЎСѓРјРјР°: {settings.subscription_price} СЂСѓР±/РјРµСЃ\n\n"
            f"вљ пёЏ РћРЅР»Р°Р№РЅ-РѕРїР»Р°С‚Р° РІСЂРµРјРµРЅРЅРѕ РЅРµРґРѕСЃС‚СѓРїРЅР°.\n"
            f"РЎРІСЏР¶РёС‚РµСЃСЊ СЃ РїРѕРґРґРµСЂР¶РєРѕР№: {settings.support_username}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    from app.services.yukassa_service import create_yukassa_payment
    try:
        payment_url = await create_yukassa_payment(tenant_id, tenant.company_name)
    except Exception as e:
        logger.error(f"create_yukassa_payment failed: {e}")
        payment_url = None

    if not payment_url:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data=f"acc:detail:{tenant_id}"
        ))
        await callback.message.edit_text(
            f"вљ пёЏ РќРµ СѓРґР°Р»РѕСЃСЊ СЃРѕР·РґР°С‚СЊ РїР»Р°С‚С‘Р¶.\n"
            f"РћР±СЂР°С‚РёС‚РµСЃСЊ РІ РїРѕРґРґРµСЂР¶РєСѓ: {settings.support_username}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"рџ’і РћРїР»Р°С‚РёС‚СЊ {settings.subscription_price} СЂСѓР±",
        url=payment_url,
    ))
    builder.row(InlineKeyboardButton(
        text="вњ… РЇ РѕРїР»Р°С‚РёР» вЂ” РїСЂРѕРІРµСЂРёС‚СЊ",
        callback_data=f"pay:check:{tenant_id}",
    ))
    builder.row(InlineKeyboardButton(
        text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data=f"acc:detail:{tenant_id}"
    ))

    await callback.message.edit_text(
        f"рџ’і <b>РћРїР»Р°С‚Р° РїРѕРґРїРёСЃРєРё</b>\n\n"
        f"рџЏў {tenant.company_name}\n"
        f"рџ’° РЎСѓРјРјР°: {settings.subscription_price} СЂСѓР±\n"
        f"рџ“… РџРµСЂРёРѕРґ: {settings.subscription_days} РґРЅРµР№\n\n"
        "РџРѕСЃР»Рµ РѕРїР»Р°С‚С‹ РїРѕРґРїРёСЃРєР° Р°РєС‚РёРІРёСЂСѓРµС‚СЃСЏ <b>Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё</b> "
        "РІ С‚РµС‡РµРЅРёРµ 1вЂ“2 РјРёРЅСѓС‚.\n"
        "РР»Рё РЅР°Р¶РјРёС‚Рµ В«РЇ РѕРїР»Р°С‚РёР»В» РґР»СЏ СЂСѓС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРё.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("reg:pay:"))
async def cb_reg_pay(callback: CallbackQuery):
    await _process_payment(callback)


@router.callback_query(F.data.startswith("acc:pay:"))
async def cb_acc_pay(callback: CallbackQuery):
    await _process_payment(callback)


@router.callback_query(F.data.startswith("pay:check:"))
async def cb_pay_check(callback: CallbackQuery):
    parsed = safe_parse(callback.data, expected_parts=3, expected_types=(str, str, int))
    if not parsed:
        await callback.answer("⚠️ Некорректный callback.", show_alert=True)
        return
    _, _, tenant_id = parsed
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
    if tenant and tenant.is_active:
        await callback.answer("вњ… РћРїР»Р°С‚Р° РїРѕРґС‚РІРµСЂР¶РґРµРЅР°!", show_alert=True)
        await _send_activation_message(callback.message, tenant, tenant.api_key)
    else:
        await callback.answer(
            "вЏі РџР»Р°С‚С‘Р¶ РµС‰С‘ РЅРµ РїРѕРґС‚РІРµСЂР¶РґС‘РЅ. РџРѕРґРѕР¶РґРёС‚Рµ 1вЂ“2 РјРёРЅСѓС‚С‹ Рё РїРѕРїСЂРѕР±СѓР№С‚Рµ СЃРЅРѕРІР°.",
            show_alert=True,
        )


# в”Ђв”Ђ API РєР»СЋС‡Рё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.callback_query(F.data.startswith("acc:keys:"))
async def cb_acc_keys(callback: CallbackQuery):
    parsed = safe_parse(callback.data, expected_parts=3, expected_types=(str, str, int))
    if not parsed:
        await callback.answer("⚠️ Некорректный callback.", show_alert=True)
        return
    _, _, tenant_id = parsed
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if not tenant or tenant.owner_tg_id != callback.from_user.id:
            await callback.answer("⛔️ Не найдено.", show_alert=True)
            return
        if not tenant.management_api_key:
            tenant.management_api_key = await repo.ensure_management_api_key(tenant_id)
            await session.commit()
            await session.refresh(tenant)
    await callback.answer()

    domain = settings.public_domain or "YOUR_DOMAIN"
    webhook_url = f"https://{domain}/api/v1/leads/tilda"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="⬅️ Назад", callback_data=f"acc:detail:{tenant_id}"
    ))

    await callback.message.edit_text(
        f"🔑 <b>API ключи — {tenant.company_name}</b>\n\n"
        f"<b>Ingest API key (X-API-Key):</b>\n"
        f"<code>{tenant.api_key or '—'}</code>\n\n"
        f"<b>Management API key (X-Management-API-Key):</b>\n"
        f"<code>{tenant.management_api_key or '—'}</code>\n\n"
        f"<b>Webhook URL для Tilda:</b>\n"
        f"<code>{webhook_url}</code>\n\n"
        "Ingest-запросы отправляйте только server-to-server с заголовком:\n"
        "<code>X-API-Key: ВАШ_КЛЮЧ</code>\n\n"
        "Management-запросы отправляйте с заголовком:\n"
        "<code>X-Management-API-Key: ВАШ_КЛЮЧ</code>\n\n"
        "⛔️ Никогда не вставляйте ingest API key в браузерный JS-код.\n"
        "Используйте Tilda webhook (настройки Tilda) или ваш backend proxy.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("acc:ref:"))
async def cb_acc_ref(callback: CallbackQuery):
    parsed = safe_parse(callback.data, expected_parts=3, expected_types=(str, str, int))
    if not parsed:
        await callback.answer("⚠️ Некорректный callback.", show_alert=True)
        return
    _, _, tenant_id = parsed
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if not tenant or tenant.owner_tg_id != callback.from_user.id:
            await callback.answer("в›”пёЏ РќРµ РЅР°Р№РґРµРЅРѕ.", show_alert=True)
            return
        stats = await repo.get_referral_stats(tenant_id)

    # РСЃРїРѕР»СЊР·СѓРµРј master_bot_username РёР· ENV РµСЃР»Рё РµСЃС‚СЊ, РёРЅР°С‡Рµ РїРѕРґР±РёСЂР°РµРј РёР· crm_bot_username
    master_username = getattr(settings, "master_bot_username", None)
    if not master_username:
        master_username = settings.crm_bot_username.replace("_bot", "_master_bot")
    ref_link = f"https://t.me/{master_username}?start=ref_{tenant.referral_code}"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="в¬…пёЏ РќР°Р·Р°Рґ", callback_data=f"acc:detail:{tenant_id}"
    ))

    await callback.answer()
    await callback.message.edit_text(
        f"рџ‘Ґ <b>Р РµС„РµСЂР°Р»СЊРЅР°СЏ РїСЂРѕРіСЂР°РјРјР°</b>\n\n"
        f"Р—Р° РєР°Р¶РґРѕРіРѕ РґСЂСѓРіР° РєРѕС‚РѕСЂС‹Р№ РѕРїР»Р°С‚РёС‚ РїРѕРґРїРёСЃРєСѓ вЂ” "
        f"РІС‹ РїРѕР»СѓС‡Р°РµС‚Рµ <b>{settings.referral_bonus_days} РґРЅРµР№ Р±РµСЃРїР»Р°С‚РЅРѕ</b>.\n\n"
        f"<b>Р’Р°С€Р° СЂРµС„РµСЂР°Р»СЊРЅР°СЏ СЃСЃС‹Р»РєР°:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"рџ“Љ <b>РЎС‚Р°С‚РёСЃС‚РёРєР°:</b>\n"
        f"РџСЂРёРіР»Р°С€РµРЅРѕ: {stats['total']}\n"
        f"РћРїР»Р°С‚РёР»Рё: {stats['paid']}\n"
        f"Р‘РѕРЅСѓСЃ РїРѕР»СѓС‡РµРЅРѕ: {stats['bonus_days_earned']} РґРЅРµР№\n\n"
        "РџРѕРґРµР»РёС‚РµСЃСЊ СЃСЃС‹Р»РєРѕР№ вЂ” Р±РѕРЅСѓСЃ РЅР°С‡РёСЃР»СЏРµС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РїСЂРё РѕРїР»Р°С‚Рµ РґСЂСѓРіР°.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# в”Ђв”Ђ РРЅСЃС‚СЂСѓРєС†РёСЏ РїРѕСЃР»Рµ Р°РєС‚РёРІР°С†РёРё в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

async def _send_activation_message(
    message: Message,
    tenant: Tenant,
    api_key: str | None,
) -> None:
    until_str = ""
    if tenant.subscription_until:
        until_str = tenant.subscription_until.strftime("%d.%m.%Y")

    crm_bot = f"@{settings.crm_bot_username}"
    domain = settings.public_domain or "YOUR_DOMAIN"
    webhook_url = f"https://{domain}/api/v1/leads/tilda"

    await message.answer(
        f"рџЋ‰ <b>Р”РѕСЃС‚СѓРї РѕС‚РєСЂС‹С‚!</b>\n\n"
        f"рџЏў {tenant.company_name}\n"
        f"рџ“… РџРѕРґРїРёСЃРєР° РґРѕ: {until_str}\n\n"
        "РќРёР¶Рµ вЂ” РёРЅСЃС‚СЂСѓРєС†РёСЏ РїРѕ РЅР°СЃС‚СЂРѕР№РєРµ. Р­С‚Рѕ Р·Р°Р№РјС‘С‚ 5 РјРёРЅСѓС‚.",
        parse_mode="HTML",
    )

    await message.answer(
        "рџ“‹ <b>РРЅСЃС‚СЂСѓРєС†РёСЏ РїРѕ РЅР°СЃС‚СЂРѕР№РєРµ TelegramCRM</b>\n\n"
        "в”Ѓв”Ѓв”Ѓ <b>РЁР°Рі 1. РЎРѕР·РґР°Р№С‚Рµ СЃСѓРїРµСЂРіСЂСѓРїРїСѓ</b>\n"
        "1. Telegram в†’ РќРѕРІР°СЏ РіСЂСѓРїРїР°\n"
        "2. РќР°Р·РѕРІРёС‚Рµ РµС‘, РЅР°РїСЂРёРјРµСЂ В«CRM РћС‚РґРµР» РїСЂРѕРґР°Р¶В»\n"
        "3. Р—Р°Р№РґРёС‚Рµ РІ РќР°СЃС‚СЂРѕР№РєРё РіСЂСѓРїРїС‹ в†’ РўРёРї в†’ <b>РЎСѓРїРµСЂРіСЂСѓРїРїР°</b>\n"
        "4. Р’РєР»СЋС‡РёС‚Рµ <b>РўРµРјС‹ (Topics)</b> РІ РЅР°СЃС‚СЂРѕР№РєР°С… РіСЂСѓРїРїС‹\n\n"
        f"в”Ѓв”Ѓв”Ѓ <b>РЁР°Рі 2. Р”РѕР±Р°РІСЊС‚Рµ CRM Р±РѕС‚Р°</b>\n"
        f"1. Р”РѕР±Р°РІСЊС‚Рµ {crm_bot} РІ РіСЂСѓРїРїСѓ\n"
        "2. РќР°Р·РЅР°С‡СЊС‚Рµ РµРіРѕ <b>Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј</b> СЃ РїСЂР°РІР°РјРё:\n"
        "   вЂў РЈРїСЂР°РІР»РµРЅРёРµ СЃРѕРѕР±С‰РµРЅРёСЏРјРё вњ…\n"
        "   вЂў РЈРґР°Р»РµРЅРёРµ СЃРѕРѕР±С‰РµРЅРёР№ вњ…\n"
        "   вЂў Р—Р°РєСЂРµРїР»РµРЅРёРµ СЃРѕРѕР±С‰РµРЅРёР№ вњ…\n"
        "   вЂў РЈРїСЂР°РІР»РµРЅРёРµ С‚РµРјР°РјРё вњ…\n\n"
        "в”Ѓв”Ѓв”Ѓ <b>РЁР°Рі 3. Р—Р°РїСѓСЃС‚РёС‚Рµ Р±РѕС‚Р°</b>\n"
        "РќР°РїРёС€РёС‚Рµ РІ РіСЂСѓРїРїРµ: /setup\n"
        "Р‘РѕС‚ СЃРѕР·РґР°СЃС‚ РІСЃРµ РЅРµРѕР±С…РѕРґРёРјС‹Рµ С‚РѕРїРёРєРё Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё.\n\n"
        "в”Ѓв”Ѓв”Ѓ <b>РЁР°Рі 4. Р”РѕР±Р°РІСЊС‚Рµ РјРµРЅРµРґР¶РµСЂРѕРІ</b>\n"
        "РћС‚РІРµС‚СЊС‚Рµ РЅР° СЃРѕРѕР±С‰РµРЅРёРµ СЃРѕС‚СЂСѓРґРЅРёРєР° РєРѕРјР°РЅРґРѕР№:\n"
        "/add_manager вЂ” РґРѕР±Р°РІРёС‚СЊ РјРµРЅРµРґР¶РµСЂР°\n"
        "/make_admin вЂ” СЃРґРµР»Р°С‚СЊ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂРѕРј CRM\n\n"
        "в”Ѓв”Ѓв”Ѓ <b>РЁР°Рі 5 (РѕРїС†РёРѕРЅР°Р»СЊРЅРѕ). Tilda РёРЅС‚РµРіСЂР°С†РёСЏ</b>\n"
        "Р—Р°СЏРІРєРё СЃ СЃР°Р№С‚Р° в†’ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РІ CRM.\n"
        "РџРѕРґСЂРѕР±РЅРѕСЃС‚Рё вЂ” /api_keys",
        parse_mode="HTML",
    )

    if api_key:
        await message.answer(
            f"🔑 <b>Ваши ключи для интеграций</b>\n\n"
            f"<b>Ingest API key (X-API-Key):</b>\n<code>{api_key}</code>\n\n"
            f"<b>Management API key (X-Management-API-Key):</b>\n"
            f"<code>{tenant.management_api_key or '—'}</code>\n\n"
            f"<b>Webhook URL (Tilda и др.):</b>\n<code>{webhook_url}</code>\n\n"
            "Ingest-запросы отправляйте только server-to-server с заголовком:\n"
            "<code>X-API-Key: ВАШ_КЛЮЧ</code>\n\n"
            "Management-запросы отправляйте с заголовком:\n"
            "<code>X-Management-API-Key: ВАШ_КЛЮЧ</code>\n\n"
            "⛔️ Не вставляйте ingest API key в client-side JS.\n"
            "Только server-side интеграция: Tilda webhook или backend proxy.\n\n"
            "⚠️ Сохраните ключи — они не должны попадать в публичный код.",
            parse_mode="HTML",
        )
    await message.answer(
        f"вќ“ <b>РќСѓР¶РЅР° РїРѕРјРѕС‰СЊ?</b>\n\n"
        f"РџРѕРґРґРµСЂР¶РєР°: {settings.support_username}\n"
        f"РџРѕСЃРјРѕС‚СЂРµС‚СЊ РєР»СЋС‡Рё: /api_keys\n"
        f"Р РµС„РµСЂР°Р»СЊРЅР°СЏ РїСЂРѕРіСЂР°РјРјР°: /referral\n"
        f"РЈРїСЂР°РІР»РµРЅРёРµ Р°РєРєР°СѓРЅС‚РѕРј: /start",
        parse_mode="HTML",
    )

    if not tenant.onboarding_completed:
        await message.answer(
            f"рџ“Њ <b>РќРµ Р·Р°Р±СѓРґСЊС‚Рµ РЅР°СЃС‚СЂРѕРёС‚СЊ РіСЂСѓРїРїСѓ!</b>\n\n"
            f"Р”РѕР±Р°РІСЊС‚Рµ {crm_bot} РІ РІР°С€Сѓ СЃСѓРїРµСЂРіСЂСѓРїРїСѓ РєР°Рє Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂР° "
            f"Рё РЅР°РїРёС€РёС‚Рµ /setup вЂ” СЌС‚Рѕ Р·Р°Р№РјС‘С‚ 1 РјРёРЅСѓС‚Сѓ.",
            parse_mode="HTML",
        )


