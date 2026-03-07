from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberOwner, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from app.bot.constants.ttl import TTL_ERROR_SEC, TTL_MENU_SEC
from app.bot.handlers.panel import ensure_panel_message
from app.bot.topic_cache import invalidate as invalidate_topic_cache
from app.bot.topic_resolver import resolve_topic_thread_id
from app.bot.topics import STATUS_TO_TOPIC_KEY, TOPIC_SPECS, TopicKey
from app.core.config import settings
from app.core.permissions import is_tg_admin
from app.db.database import AsyncSessionLocal
from app.db.models.lead import LeadStatus, ManagerRole
from app.db.repositories.lead_repository import LeadRepository
from app.db.repositories.tenant_topics import TenantTopicRepository
from app.services.yukassa_service import _create_yukassa_payment
from app.telegram.safe_sender import TelegramSafeSender

router = Router()


class TenantRegistrationState(StatesGroup):
    waiting_for_company_name = State()


def _get_topic_spec(key: TopicKey):
    for spec in TOPIC_SPECS:
        if spec.key == key:
            return spec
    return None


async def _probe_topic_thread(sender: TelegramSafeSender, chat_id: int, thread_id: int) -> bool:
    await sender.get_chat(chat_id)
    probe = await sender.send_text(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text=".",
    )
    try:
        await sender.delete_message(
            chat_id=chat_id,
            message_id=probe.message_id,
            thread_id=probe.message_thread_id,
        )
    except Exception as exc:
        logger.warning(f"Could not delete probe message in thread {thread_id}: {exc}")
    return True


async def _ensure_owner_registered(user_id: int, full_name: str, username: str | None):
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        existing = await repo.get_manager_by_tg_id(user_id)
        if not existing:
            await repo.create_manager(
                tg_id=user_id,
                name=full_name,
                username=username,
                role=ManagerRole.ADMIN,
            )
            await session.commit()
            logger.info(f"Auto-registered owner as admin: {full_name} (tg_id={user_id})")


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, sender: TelegramSafeSender):
    """Регистрация нового тенанта или приветствие существующего."""
    if message.chat.type == "private":
        await message.answer("ℹ️ Добавьте бота в вашу Telegram-группу как администратора.")
        return

    async with AsyncSessionLocal() as session:
        from app.db.repositories.tenant_repository import TenantRepository

        repo = TenantRepository(session)
        existing = await repo.get_by_group_id(message.chat.id)
        if existing:
            if existing.is_active:
                await sender.send_ephemeral_text(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    text="✅ CRM активна. Используйте /setup для настройки.",
                    ttl_sec=30,
                )
            else:
                await sender.send_ephemeral_text(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    text="⏰ Подписка истекла. Напишите /pay для продления.",
                    ttl_sec=60,
                )
            return

    if message.from_user is None:
        return

    try:
        member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in ("creator", "administrator"):
            await message.answer("⛔️ Только администратор группы может зарегистрировать CRM.")
            return
    except Exception:
        return

    await state.set_state(TenantRegistrationState.waiting_for_company_name)
    await state.update_data(
        reg_group_id=message.chat.id,
        reg_owner_tg_id=message.from_user.id,
        reg_chat_id=message.chat.id,
        reg_thread_id=message.message_thread_id,
    )
    await sender.send_ephemeral_text(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="👋 Добро пожаловать в TelegramCRM!\n\nВведите название вашей компании:",
        ttl_sec=300,
    )


@router.message(TenantRegistrationState.waiting_for_company_name)
async def handle_company_name(message: Message, state: FSMContext, sender: TelegramSafeSender):
    data = await state.get_data()
    group_id = data.get("reg_group_id")
    owner_tg_id = data.get("reg_owner_tg_id")
    chat_id = data.get("reg_chat_id")
    thread_id = data.get("reg_thread_id")
    await state.clear()

    company_name = (message.text or "").strip()[:255]
    if not company_name:
        return

    async with AsyncSessionLocal() as session:
        from app.db.repositories.tenant_repository import TenantRepository
        from master_bot.notify import notify_admin

        repo = TenantRepository(session)
        tenant = await repo.create(
            group_id=group_id,
            owner_tg_id=owner_tg_id,
            company_name=company_name,
        )
        await session.commit()

        await notify_admin(
            f"🆕 Новый клиент: <b>{company_name}</b>\n"
            f"ID тенанта: {tenant.id}\n"
            f"Group: {group_id}"
        )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"🆓 Пробный период {settings.trial_days} дней",
            callback_data=f"reg:trial:{tenant.id}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"💳 Оплатить {settings.subscription_price} руб/мес",
            callback_data=f"reg:pay:{tenant.id}",
        ),
    )
    await sender.send_message(
        chat_id=chat_id,
        message_thread_id=thread_id,
        text=f"✅ Компания <b>{company_name}</b> зарегистрирована!\n\nВыберите как начать:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("reg:trial:"))
async def cb_reg_trial(callback: CallbackQuery, sender: TelegramSafeSender):
    tenant_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        from app.db.repositories.tenant_repository import TenantRepository
        from master_bot.notify import notify_admin

        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if not tenant or tenant.trial_used:
            await callback.answer("⚠️ Пробный период уже использован.", show_alert=True)
            return
        await repo.activate_trial(tenant_id, days=settings.trial_days)
        await session.commit()
        await notify_admin(f"🆓 Пробный период: <b>{tenant.company_name}</b> (ID:{tenant_id})")

    await callback.answer()
    from datetime import datetime, timezone, timedelta

    until = (datetime.now(timezone.utc) + timedelta(days=settings.trial_days)).strftime("%d.%m.%Y")
    if callback.message:
        await callback.message.edit_text(
            f"✅ Пробный период активирован до {until}!\n\nТеперь выполните /setup для настройки топиков.",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("reg:pay:"))
async def cb_reg_pay(callback: CallbackQuery, sender: TelegramSafeSender):
    tenant_id = int(callback.data.split(":")[2])
    await callback.answer()
    payment_url = await _create_yukassa_payment(tenant_id)
    if not payment_url:
        if callback.message:
            await callback.message.edit_text("⚠️ Не удалось создать платёж. Обратитесь в поддержку.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"💳 Оплатить {settings.subscription_price} руб",
            url=payment_url,
        )
    )
    if callback.message:
        await callback.message.edit_text(
            "💳 Для оплаты нажмите кнопку ниже.\nПосле оплаты подписка активируется автоматически.",
            reply_markup=builder.as_markup(),
        )


@router.message(Command("setup"))
async def cmd_setup(message: Message, sender: TelegramSafeSender):
    chat_id = message.chat.id

    if message.from_user is None:
        return

    target_tenant = None

    # Ищем тенант, привязанный к этому владельцу.
    async with AsyncSessionLocal() as session:
        from app.db.repositories.tenant_repository import TenantRepository
        from master_bot.notify import notify_admin

        repo = TenantRepository(session)

        # Получаем все тенанты по owner_tg_id
        tenants = await repo.get_by_owner(message.from_user.id)

        # Берём первый подходящий: либо ещё не привязан к группе (group_id == 0),
        # либо уже привязан к текущей группе (group_id == chat_id).
        for t in tenants:
            if t.group_id == 0 or t.group_id == chat_id:
                target_tenant = t
                break

        if not target_tenant:
            # Пользователь пытается настроить CRM из группы, но регистрация была в мастер-боте.
            # Показываем куда идти.
            await sender.send_ephemeral_text(
                chat_id=chat_id,
                message_thread_id=message.message_thread_id,
                text=(
                    "❌ Вы ещё не зарегистрировали CRM через мастер-бот.\n"
                    f"Перейдите в мастер-бот: @{settings.crm_bot_username.replace('crm_bot', 'crm_master_bot')}"
                ),
                ttl_sec=60,
            )
            return

        # Если тенант ещё не привязан к группе — привязываем.
        if target_tenant.group_id == 0:
            await repo.bind_group(target_tenant.id, chat_id)
            await session.commit()
            await notify_admin(
                "✅ Группа привязана к CRM\n"
                f"🏢 {target_tenant.company_name}\n"
                f"🆔 group_id: {chat_id}"
            )

    if not await is_tg_admin(sender, chat_id, message.from_user.id):
        await sender.send_ephemeral_text(
            chat_id=chat_id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Только администратор группы может выполнять /setup.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    chat = await sender.get_chat(chat_id)
    if not getattr(chat, "is_forum", False):
        await sender.send_ephemeral_text(
            chat_id=chat_id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Включите в группе темы (форум), чтобы настроить CRM.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    member = await sender.get_chat_member(chat_id, message.from_user.id)
    if isinstance(member, ChatMemberOwner):
        await _ensure_owner_registered(
            message.from_user.id,
            message.from_user.full_name,
            message.from_user.username,
        )

    progress = await sender.send_ephemeral_text(
        chat_id=chat_id,
        message_thread_id=message.message_thread_id,
        text="⚙️ Настраиваю топики...",
        ttl_sec=TTL_MENU_SEC,
    )

    created: list[tuple[str, str, int]] = []
    errors: list[tuple[str, str]] = []

    async with AsyncSessionLocal() as session:
        repo = TenantTopicRepository(session)
        existing_map = await repo.get_topic_map(chat_id)

        for spec in TOPIC_SPECS:
            existing_thread = existing_map.get(spec.key.value)
            if existing_thread:
                try:
                    try:
                        await _probe_topic_thread(sender, chat_id, existing_thread)
                    except TelegramBadRequest as e:
                        if "message thread not found" in str(e).lower():
                            existing_thread = None
                            existing_map.pop(spec.key.value, None)
                        else:
                            raise
                except Exception as exc:
                    errors.append((spec.title, str(exc)))
                    logger.error(f"Failed to validate topic '{spec.title}': {exc}")
                    continue

            if existing_thread:
                await repo.upsert_topic(
                    chat_id=chat_id,
                    key=spec.key.value,
                    thread_id=existing_thread,
                    title=spec.title,
                )
                continue

            try:
                topic = await sender.create_forum_topic(chat_id, spec.title)
                await repo.upsert_topic(
                    chat_id=chat_id,
                    key=spec.key.value,
                    thread_id=topic.message_thread_id,
                    title=spec.title,
                )
                created.append((spec.title, spec.key.value, topic.message_thread_id))
                existing_map[spec.key.value] = topic.message_thread_id
                logger.info(f"Topic created: {spec.title} -> id={topic.message_thread_id}")
            except Exception as exc:
                errors.append((spec.title, str(exc)))
                logger.error(f"Failed to create topic '{spec.title}': {exc}")

        await session.commit()

    invalidate_topic_cache(chat_id)

    topic_managers_id = existing_map.get(TopicKey.MANAGERS.value)
    if topic_managers_id:
        try:
            await ensure_panel_message(sender, chat_id, topic_managers_id)
            logger.info(f"Panel message ensured in topic {topic_managers_id}")
        except Exception as exc:
            errors.append(("Панель управления", str(exc)))
            logger.error(f"Failed to ensure panel message: {exc}")

    summary_lines = ["✅ Настройка завершена."]
    if created:
        summary_lines.append(f"Создано топиков: {len(created)}.")
    if errors:
        summary_lines.append("Ошибки:")
        for name, err in errors:
            summary_lines.append(f"- {name}: {err}")
    summary_lines.append("Готово.")

    await sender.edit_text(
        chat_id=progress.chat.id,
        message_id=progress.message_id,
        text="\n".join(summary_lines),
        thread_id=progress.message_thread_id,
    )
    await sender.schedule_delete(
        chat_id=progress.chat.id,
        message_id=progress.message_id,
        thread_id=progress.message_thread_id,
        ttl_sec=TTL_MENU_SEC,
    )

    try:
        await sender.delete_message(
            chat_id=chat_id,
            message_id=message.message_id,
            thread_id=message.message_thread_id,
        )
    except Exception:
        pass

    try:
        await _ensure_topic_menus(sender, chat_id)
        logger.info("Topic menus ensured")
    except Exception as exc:
        logger.error(f"Failed to ensure topic menus: {exc}")

    if not errors and target_tenant:
        async with AsyncSessionLocal() as session:
            from app.db.repositories.tenant_repository import TenantRepository

            repo = TenantRepository(session)
            await repo.complete_onboarding(target_tenant.id)
            await session.commit()


@router.message(Command("add_manager"))
async def cmd_add_manager(message: Message, sender: TelegramSafeSender, tenant=None):
    group_id = tenant.group_id if tenant else None
    if not group_id or message.chat.id != group_id:
        return

    if message.from_user is None:
        return

    if not await is_tg_admin(sender, group_id, message.from_user.id):
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Только администраторы группы могут назначать менеджеров.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    if not message.reply_to_message:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="👆 Ответьте на сообщение участника командой /add_manager.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    target = message.reply_to_message.from_user
    if not target:
        return

    # Лимиты по плану
    if tenant and tenant.max_managers != -1:
        async with AsyncSessionLocal() as session:
            repo = LeadRepository(session)
            current_count = await repo.count_active_managers(tenant_id=tenant.id)
        if current_count >= tenant.max_managers:
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text=(
                    f"⚠️ В вашем плане максимум {tenant.max_managers} менеджеров ({tenant.plan}). "
                    "Оплатите расширение или смените тариф."
                ),
                ttl_sec=30,
            )
            return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        existing = await repo.get_manager_by_tg_id(target.id)

        if existing:
            if existing.is_active:
                await sender.send_ephemeral_text(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    text=f"ℹ️ {target.full_name} уже является менеджером.",
                    ttl_sec=TTL_MENU_SEC,
                )
            else:
                existing.is_active = True
                await session.commit()
                await sender.send_ephemeral_text(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    text=f"✅ {target.full_name} восстановлен как менеджер.",
                    ttl_sec=TTL_MENU_SEC,
                )
            return

        await repo.create_manager(
            tg_id=target.id,
            name=target.full_name,
            username=target.username,
            role=ManagerRole.MANAGER,
            tenant_id=tenant.id if tenant else None,
        )
        await session.commit()

    username = f"@{target.username}" if target.username else "—"
    await sender.send_ephemeral_text(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=(
            f"✅ {target.full_name} назначен менеджером.\n"
            f"Username: {username}\n\n"
            "Чтобы дать права администратора — ответьте на его сообщение: /make_admin"
        ),
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


@router.message(Command("make_admin"))
async def cmd_make_admin(message: Message, sender: TelegramSafeSender, tenant=None):
    group_id = tenant.group_id if tenant else None
    if not group_id or message.chat.id != group_id:
        return

    if message.from_user is None:
        return

    member = await sender.get_chat_member(group_id, message.from_user.id)
    if not isinstance(member, ChatMemberOwner):
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Только владелец группы может назначать CRM-администраторов.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    if not message.reply_to_message:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="👆 Ответьте на сообщение пользователя командой /make_admin.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    target = message.reply_to_message.from_user
    if not target:
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await repo.set_manager_role(target.id, ManagerRole.ADMIN)
        if not manager:
            manager = await repo.create_manager(
                tg_id=target.id,
                name=target.full_name,
                username=target.username,
                role=ManagerRole.ADMIN,
                tenant_id=tenant.id if tenant else None,
            )
        await session.commit()

    await sender.send_ephemeral_text(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=(
            f"👑 {target.full_name} теперь CRM-администратор.\n"
            "Может назначать менеджеров, делать выгрузки и смотреть статистику."
        ),
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


@router.message(Command("remove_manager"))
async def cmd_remove_manager(message: Message, sender: TelegramSafeSender, tenant=None):
    group_id = tenant.group_id if tenant else None
    if not group_id or message.chat.id != group_id:
        return

    if message.from_user is None:
        return

    if not await is_tg_admin(sender, group_id, message.from_user.id):
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Только администраторы группы могут убирать менеджеров.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    if not message.reply_to_message:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="👆 Ответьте на сообщение пользователя командой /remove_manager.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    target = message.reply_to_message.from_user
    if not target:
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        ok = await repo.deactivate_manager(target.id)
        await session.commit()

    if ok:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=f"✅ {target.full_name} удалён из менеджеров.",
            ttl_sec=TTL_MENU_SEC,
        )
    else:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=f"ℹ️ {target.full_name} не найден в списке менеджеров.",
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


@router.message(Command("managers"))
async def cmd_managers(message: Message, sender: TelegramSafeSender, tenant=None):
    group_id = tenant.group_id if tenant else None
    if not group_id or message.chat.id != group_id:
        return

    if message.from_user is None:
        return

    if not await is_tg_admin(sender, group_id, message.from_user.id):
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Только администраторы могут просматривать список.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        managers = await repo.get_all_managers(tenant_id=tenant.id if tenant else None)

    if not managers:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="ℹ️ Менеджеров пока нет.\nДобавьте через /add_manager (ответом на сообщение).",
            ttl_sec=TTL_MENU_SEC,
        )
        return

    lines = ["👥 Команда:"]
    for manager in managers:
        role = "Администратор" if manager.is_admin else "Менеджер"
        username = f"@{manager.tg_username}" if manager.tg_username else "—"
        icon = "👑" if manager.is_admin else "👤"
        lines.append(f"{icon} {manager.name} ({role}) {username}")

    lines.append("")
    lines.append("/add_manager — добавить (ответом на сообщение)")
    lines.append("/make_admin — дать права администратора")
    lines.append("/remove_manager — убрать")

    await sender.send_ephemeral_text(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="\n".join(lines),
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


async def _ensure_topic_menus(sender: TelegramSafeSender, chat_id: int):
    async with AsyncSessionLocal() as session:
        repo = TenantTopicRepository(session)
        for status, key in STATUS_TO_TOPIC_KEY.items():
            try:
                topic_id = await resolve_topic_thread_id(
                    chat_id,
                    key,
                    session,
                    sender=sender,
                    thread_id=None,
                )
                if topic_id:
                    try:
                        await _probe_topic_thread(sender, chat_id, topic_id)
                    except TelegramBadRequest as e:
                        if "message thread not found" in str(e).lower():
                            topic_id = None
                        else:
                            raise

                if not topic_id:
                    spec = _get_topic_spec(key)
                    if not spec:
                        logger.error(f"Topic spec not found for {key}")
                        continue
                    topic = await sender.create_forum_topic(chat_id, spec.title)
                    await repo.upsert_topic(
                        chat_id=chat_id,
                        key=key.value,
                        thread_id=topic.message_thread_id,
                        title=spec.title,
                    )
                    await session.commit()
                    invalidate_topic_cache(chat_id)
                    topic_id = topic.message_thread_id

                if topic_id:
                    await _post_topic_menu(sender, chat_id, topic_id, status)
            except Exception as exc:
                logger.error(f"Failed to ensure menu for {key.value}: {exc}")


def _build_topic_menu(status: LeadStatus) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if status == LeadStatus.NEW:
        builder.row(
            InlineKeyboardButton(
                text="➕ Создать заявку",
                callback_data="menu:create",
            ),
            InlineKeyboardButton(
                text="📆 Выбрать период",
                callback_data="menu:period:new",
            ),
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text="📆 Выбрать период",
                callback_data=f"menu:period:{status.value}",
            ),
        )
    return builder


async def _post_topic_menu(
    sender: TelegramSafeSender,
    chat_id: int,
    topic_id: int,
    status: LeadStatus,
):
    text = "Меню действий"
    builder = _build_topic_menu(status)
    msg = await sender.send_message(
        chat_id=chat_id,
        message_thread_id=topic_id,
        text=text,
        reply_markup=builder.as_markup(),
    )
    try:
        await sender.pin_chat_message(chat_id, msg.message_id)
    except Exception as exc:
        logger.warning(f"Could not pin topic menu in topic {topic_id}: {exc}")
