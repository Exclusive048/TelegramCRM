from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.models.tenant import Tenant
from app.db.repositories.tenant_repository import TenantRepository
from master_bot.notify import notify_admin

router = Router()


class RegState(StatesGroup):
    waiting_for_name = State()


# ── Вспомогательные функции ────────────────────────────────────────────────────

def _status_line(tenant: Tenant) -> str:
    icon = "✅" if tenant.is_active else "🔴"
    until = ""
    if tenant.subscription_until:
        until = f" до {tenant.subscription_until.strftime('%d.%m.%Y')}"
    plan_map = {"trial": "Пробный", "base": "Базовый", "pro": "Pro"}
    plan = plan_map.get(tenant.plan, tenant.plan or "—")
    return f"{icon} <b>{tenant.company_name}</b> — {plan}{until}"


def _tenant_detail_text(tenant: Tenant) -> str:
    now = datetime.now(timezone.utc)
    status = "✅ Активна" if tenant.is_active else "🔴 Неактивна"
    until = "—"
    days_left_str = ""
    if tenant.subscription_until:
        until = tenant.subscription_until.strftime("%d.%m.%Y")
        delta = (tenant.subscription_until - now).days
        days_left_str = f" (осталось {delta} дн.)" if delta >= 0 else " (истекла)"
    plan_map = {
        "trial": "Пробный",
        "base": "Базовый 990 руб/мес",
        "pro": "Pro 2490 руб/мес",
    }
    plan = plan_map.get(tenant.plan, tenant.plan or "—")
    onboarding = "✅ Настроена" if tenant.onboarding_completed else "⚠️ Ожидает /setup"
    return (
        f"🏢 <b>{tenant.company_name}</b>\n"
        f"📊 Статус: {status}\n"
        f"💰 Тариф: {plan}\n"
        f"⏰ Подписка до: {until}{days_left_str}\n"
        f"🔧 Группа CRM: {onboarding}\n"
        f"🔑 Реф. код: <code>{tenant.referral_code or '—'}</code>\n"
    )


def _account_keyboard(tenant: Tenant) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    now = datetime.now(timezone.utc)

    # Кнопка оплаты — скрывать только если подписка активна и > 7 дней
    show_pay = True
    if (
        tenant.is_active
        and tenant.subscription_until
        and (tenant.subscription_until - now).days > 7
    ):
        show_pay = False

    if show_pay:
        label = "💳 Продлить подписку" if tenant.is_active else "💳 Оплатить подписку"
        b.row(InlineKeyboardButton(
            text=label,
            callback_data=f"acc:pay:{tenant.id}",
        ))

    if tenant.api_key:
        b.row(InlineKeyboardButton(
            text="🔑 Мои API ключи",
            callback_data=f"acc:keys:{tenant.id}",
        ))

    b.row(InlineKeyboardButton(
        text="👥 Реферальная программа",
        callback_data=f"acc:ref:{tenant.id}",
    ))
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="main:back"))
    return b


# ── /start ─────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: CommandObject):
    if message.chat.type != "private":
        return

    referred_code: str | None = None
    if command.args and command.args.startswith("ref_"):
        referred_code = command.args[4:].upper()

    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenants = await repo.get_by_owner(message.from_user.id)

    if tenants:
        await _show_my_accounts(message, tenants)
        return

    await state.set_state(RegState.waiting_for_name)
    await state.update_data(referred_code=referred_code)

    ref_note = ""
    if referred_code:
        ref_note = "\n\n🎁 Вы пришли по реферальной ссылке — при первой оплате получите бонус!"

    await message.answer(
        "👋 Добро пожаловать в <b>TelegramCRM</b>!\n\n"
        "CRM-система прямо в Telegram: заявки, менеджеры, аналитика, воронка продаж."
        f"{ref_note}\n\n"
        "Для начала введите название вашей компании или проекта:",
        parse_mode="HTML",
    )


# ── Ввод названия компании ─────────────────────────────────────────────────────

@router.message(RegState.waiting_for_name)
async def handle_company_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Название слишком короткое. Минимум 2 символа:")
        return
    if len(name) > 100:
        await message.answer("Слишком длинное. Максимум 100 символов:")
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
        tenant = await repo.create(
            owner_tg_id=message.from_user.id,
            company_name=name,
            referred_by_id=referrer_id,
        )
        await session.commit()
        tenant_id = tenant.id

    await notify_admin(
        f"🆕 Новый клиент!\n"
        f"🏢 <b>{name}</b>\n"
        f"👤 @{message.from_user.username or '—'} (id:{message.from_user.id})\n"
        f"🆔 tenant_id: {tenant_id}"
        + (f"\n🔗 Пришёл по реф. коду: {referred_code}" if referred_code else "")
    )

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"🆓 Пробный период {settings.trial_days} дней — бесплатно",
        callback_data=f"reg:trial:{tenant_id}",
    ))
    builder.row(InlineKeyboardButton(
        text=f"💳 Оплатить сразу — {settings.subscription_price} руб/мес",
        callback_data=f"reg:pay:{tenant_id}",
    ))

    await message.answer(
        f"✅ Аккаунт <b>{name}</b> создан!\n\n"
        "Выберите как начать работу:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ── Список аккаунтов ───────────────────────────────────────────────────────────

async def _show_my_accounts(message_or_callback, tenants: list[Tenant]) -> None:
    text = "📋 <b>Ваши аккаунты CRM:</b>\n\n"
    for t in tenants:
        text += _status_line(t) + "\n"

    builder = InlineKeyboardBuilder()
    for t in tenants:
        icon = "✅" if t.is_active else "🔴"
        builder.row(InlineKeyboardButton(
            text=f"{icon} {t.company_name}",
            callback_data=f"acc:detail:{t.id}",
        ))
    builder.row(InlineKeyboardButton(
        text="➕ Зарегистрировать ещё",
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
        tenants = await repo.get_by_owner(callback.from_user.id)
    await _show_my_accounts(callback, tenants)


@router.callback_query(F.data == "main:new")
async def cb_main_new(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(RegState.waiting_for_name)
    # answer() а не edit_text() — иначе FSM не подхватит следующее сообщение
    await callback.message.answer("Введите название нового аккаунта CRM:")


# ── Карточка аккаунта ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:detail:"))
async def cb_acc_detail(callback: CallbackQuery):
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
    if not tenant or tenant.owner_tg_id != callback.from_user.id:
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        _tenant_detail_text(tenant),
        reply_markup=_account_keyboard(tenant).as_markup(),
        parse_mode="HTML",
    )


# ── Пробный период ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("reg:trial:"))
async def cb_reg_trial(callback: CallbackQuery):
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if not tenant or tenant.owner_tg_id != callback.from_user.id:
            await callback.answer("Ошибка доступа", show_alert=True)
            return
        if tenant.trial_used:
            await callback.answer("Пробный период уже использован.", show_alert=True)
            return
        api_key = await repo.activate_trial(tenant_id, days=settings.trial_days)
        await session.commit()
        await session.refresh(tenant)

    await callback.answer("✅ Пробный период активирован!")
    await notify_admin(
        f"🆓 Пробный период\n🏢 <b>{tenant.company_name}</b> (ID:{tenant_id})"
    )
    await _send_activation_message(callback.message, tenant, api_key)


# ── Оплата ─────────────────────────────────────────────────────────────────────

async def _process_payment(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    tenant_id = int(parts[2])

    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)

    if not tenant or tenant.owner_tg_id != callback.from_user.id:
        await callback.answer("Ошибка доступа", show_alert=True)
        return

    await callback.answer()

    # ЮКасса не настроена — показать заглушку
    if not settings.yukassa_shop_id or not settings.yukassa_secret_key:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(
            text="⬅️ Назад", callback_data=f"acc:detail:{tenant_id}"
        ))
        await callback.message.edit_text(
            f"💳 <b>Оплата подписки</b>\n\n"
            f"🏢 {tenant.company_name}\n"
            f"💰 Сумма: {settings.subscription_price} руб/мес\n\n"
            f"⚠️ Онлайн-оплата временно недоступна.\n"
            f"Свяжитесь с поддержкой: {settings.support_username}",
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
            text="⬅️ Назад", callback_data=f"acc:detail:{tenant_id}"
        ))
        await callback.message.edit_text(
            f"⚠️ Не удалось создать платёж.\n"
            f"Обратитесь в поддержку: {settings.support_username}",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"💳 Оплатить {settings.subscription_price} руб",
        url=payment_url,
    ))
    builder.row(InlineKeyboardButton(
        text="✅ Я оплатил — проверить",
        callback_data=f"pay:check:{tenant_id}",
    ))
    builder.row(InlineKeyboardButton(
        text="⬅️ Назад", callback_data=f"acc:detail:{tenant_id}"
    ))

    await callback.message.edit_text(
        f"💳 <b>Оплата подписки</b>\n\n"
        f"🏢 {tenant.company_name}\n"
        f"💰 Сумма: {settings.subscription_price} руб\n"
        f"📅 Период: {settings.subscription_days} дней\n\n"
        "После оплаты подписка активируется <b>автоматически</b> "
        "в течение 1–2 минут.\n"
        "Или нажмите «Я оплатил» для ручной проверки.",
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
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
    if tenant and tenant.is_active:
        await callback.answer("✅ Оплата подтверждена!", show_alert=True)
        await _send_activation_message(callback.message, tenant, tenant.api_key)
    else:
        await callback.answer(
            "⏳ Платёж ещё не подтверждён. Подождите 1–2 минуты и попробуйте снова.",
            show_alert=True,
        )


# ── API ключи ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:keys:"))
async def cb_acc_keys(callback: CallbackQuery):
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
    if not tenant or tenant.owner_tg_id != callback.from_user.id:
        await callback.answer("Не найдено", show_alert=True)
        return
    await callback.answer()

    domain = settings.public_domain or "YOUR_DOMAIN"
    webhook_url = f"https://{domain}/api/v1/leads/tilda"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="⬅️ Назад", callback_data=f"acc:detail:{tenant_id}"
    ))

    await callback.message.edit_text(
        f"🔑 <b>API ключи — {tenant.company_name}</b>\n\n"
        f"<b>Ваш API ключ:</b>\n"
        f"<code>{tenant.api_key}</code>\n\n"
        f"<b>Webhook URL для Tilda:</b>\n"
        f"<code>{webhook_url}</code>\n\n"
        "Вставьте API ключ в заголовок запроса:\n"
        "<code>X-API-Key: ВАШ_КЛЮЧ</code>\n\n"
        "⚠️ Не передавайте ключ третьим лицам.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ── Реферальная программа ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:ref:"))
async def cb_acc_ref(callback: CallbackQuery):
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if not tenant or tenant.owner_tg_id != callback.from_user.id:
            await callback.answer("Не найдено", show_alert=True)
            return
        stats = await repo.get_referral_stats(tenant_id)

    # Используем master_bot_username из ENV если есть, иначе подбираем из crm_bot_username
    master_username = getattr(settings, "master_bot_username", None)
    if not master_username:
        master_username = settings.crm_bot_username.replace("_bot", "_master_bot")
    ref_link = f"https://t.me/{master_username}?start=ref_{tenant.referral_code}"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="⬅️ Назад", callback_data=f"acc:detail:{tenant_id}"
    ))

    await callback.answer()
    await callback.message.edit_text(
        f"👥 <b>Реферальная программа</b>\n\n"
        f"За каждого друга который оплатит подписку — "
        f"вы получаете <b>{settings.referral_bonus_days} дней бесплатно</b>.\n\n"
        f"<b>Ваша реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"Приглашено: {stats['total']}\n"
        f"Оплатили: {stats['paid']}\n"
        f"Бонус получено: {stats['bonus_days_earned']} дней\n\n"
        "Поделитесь ссылкой — бонус начисляется автоматически при оплате друга.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ── Инструкция после активации ─────────────────────────────────────────────────

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
        f"🎉 <b>Доступ открыт!</b>\n\n"
        f"🏢 {tenant.company_name}\n"
        f"📅 Подписка до: {until_str}\n\n"
        "Ниже — инструкция по настройке. Это займёт 5 минут.",
        parse_mode="HTML",
    )

    await message.answer(
        "📋 <b>Инструкция по настройке TelegramCRM</b>\n\n"
        "━━━ <b>Шаг 1. Создайте супергруппу</b>\n"
        "1. Telegram → Новая группа\n"
        "2. Назовите её, например «CRM Отдел продаж»\n"
        "3. Зайдите в Настройки группы → Тип → <b>Супергруппа</b>\n"
        "4. Включите <b>Темы (Topics)</b> в настройках группы\n\n"
        f"━━━ <b>Шаг 2. Добавьте CRM бота</b>\n"
        f"1. Добавьте {crm_bot} в группу\n"
        "2. Назначьте его <b>администратором</b> с правами:\n"
        "   • Управление сообщениями ✅\n"
        "   • Удаление сообщений ✅\n"
        "   • Закрепление сообщений ✅\n"
        "   • Управление темами ✅\n\n"
        "━━━ <b>Шаг 3. Запустите бота</b>\n"
        "Напишите в группе: /setup\n"
        "Бот создаст все необходимые топики автоматически.\n\n"
        "━━━ <b>Шаг 4. Добавьте менеджеров</b>\n"
        "Ответьте на сообщение сотрудника командой:\n"
        "/add_manager — добавить менеджера\n"
        "/make_admin — сделать администратором CRM\n\n"
        "━━━ <b>Шаг 5 (опционально). Tilda интеграция</b>\n"
        "Заявки с сайта → автоматически в CRM.\n"
        "Подробности — /api_keys",
        parse_mode="HTML",
    )

    if api_key:
        await message.answer(
            f"🔑 <b>Ваши ключи для интеграций</b>\n\n"
            f"<b>API ключ:</b>\n<code>{api_key}</code>\n\n"
            f"<b>Webhook URL (Tilda и др.):</b>\n<code>{webhook_url}</code>\n\n"
            "Добавьте API ключ в заголовок запроса:\n"
            "<code>X-API-Key: ВАШ_КЛЮЧ</code>\n\n"
            "⚠️ Сохраните ключ — он не будет показан повторно в открытом виде.\n"
            "Посмотреть снова: /api_keys",
            parse_mode="HTML",
        )

    await message.answer(
        f"❓ <b>Нужна помощь?</b>\n\n"
        f"Поддержка: {settings.support_username}\n"
        f"Посмотреть ключи: /api_keys\n"
        f"Реферальная программа: /referral\n"
        f"Управление аккаунтом: /start",
        parse_mode="HTML",
    )

    if not tenant.onboarding_completed:
        await message.answer(
            f"📌 <b>Не забудьте настроить группу!</b>\n\n"
            f"Добавьте {crm_bot} в вашу супергруппу как администратора "
            f"и напишите /setup — это займёт 1 минуту.",
            parse_mode="HTML",
        )