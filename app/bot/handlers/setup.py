from aiogram import Router
from aiogram.filters import Command
from aiogram.types import ChatMemberOwner, InlineKeyboardButton, Message
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
from app.telegram.safe_sender import TelegramSafeSender

router = Router()


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


@router.message(Command("setup"))
async def cmd_setup(message: Message, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⚠️ Команда работает только внутри CRM-группы.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return
    if not await is_tg_admin(sender, settings.crm_group_id, message.from_user.id):
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Только администраторы группы могут использовать /setup.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    chat = await sender.get_chat(settings.crm_group_id)
    if not getattr(chat, "is_forum", False):
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⚠️ В этой группе не включены темы (Forum).",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    member = await sender.get_chat_member(settings.crm_group_id, message.from_user.id)
    if isinstance(member, ChatMemberOwner):
        await _ensure_owner_registered(
            message.from_user.id,
            message.from_user.full_name,
            message.from_user.username,
        )

    progress = await sender.send_ephemeral_text(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="⏳ Создаю топики...",
        ttl_sec=TTL_MENU_SEC,
    )
    created: list[tuple[str, str, int]] = []
    errors: list[tuple[str, str]] = []
    chat_id = message.chat.id

    async with AsyncSessionLocal() as session:
        repo = TenantTopicRepository(session)
        existing_map = await repo.get_topic_map(chat_id)

        for spec in TOPIC_SPECS:
            existing_thread = existing_map.get(spec.key.value)
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
            errors.append(("Пульт управления", str(exc)))
            logger.error(f"Failed to ensure panel message: {exc}")

    summary_lines = ["✅ Топики созданы."]
    if created:
        summary_lines.append(f"Создано новых: {len(created)}.")
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
            chat_id=message.chat.id,
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


@router.message(Command("add_manager"))
async def cmd_add_manager(message: Message, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        return

    if not await is_tg_admin(sender, settings.crm_group_id, message.from_user.id):
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
            text="👆 Ответьте на сообщение участника командой /add_manager.",  # FIXED #15
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    target = message.reply_to_message.from_user

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        existing = await repo.get_manager_by_tg_id(target.id)

        if existing:
            if existing.is_active:
                await sender.send_ephemeral_text(
                    chat_id=message.chat.id,
                    message_thread_id=message.message_thread_id,
                    text=f"ℹ️ {target.full_name} уже является менеджером.",  # FIXED #15
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
async def cmd_make_admin(message: Message, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        return

    member = await sender.get_chat_member(settings.crm_group_id, message.from_user.id)
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
            text="👆 Ответьте на сообщение пользователя командой /make_admin.",  # FIXED #15
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    target = message.reply_to_message.from_user

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await repo.set_manager_role(target.id, ManagerRole.ADMIN)
        if not manager:
            manager = await repo.create_manager(
                tg_id=target.id,
                name=target.full_name,
                username=target.username,
                role=ManagerRole.ADMIN,
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
async def cmd_remove_manager(message: Message, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        return

    if not await is_tg_admin(sender, settings.crm_group_id, message.from_user.id):
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
            text="👆 Ответьте на сообщение пользователя командой /remove_manager.",  # FIXED #15
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    target = message.reply_to_message.from_user

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
            text=f"ℹ️ {target.full_name} не найден в списке менеджеров.",  # FIXED #15
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
async def cmd_managers(message: Message, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        return

    if not await is_tg_admin(sender, settings.crm_group_id, message.from_user.id):
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Только администраторы могут просматривать список.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        managers = await repo.get_all_managers()

    if not managers:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="👥 Менеджеров пока нет.\nДобавь через /add_manager (ответом на сообщение).",
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
        for status, key in STATUS_TO_TOPIC_KEY.items():
            topic_id = await resolve_topic_thread_id(
                chat_id,
                key,
                session,
                sender=sender,
                thread_id=None,
            )
            if topic_id:
                await _post_topic_menu(sender, chat_id, topic_id, status)


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
    msg = await sender.send_message(  # FIXED #3
        chat_id=chat_id,
        message_thread_id=topic_id,
        text=text,
        reply_markup=builder.as_markup(),
    )
    try:
        await sender.pin_chat_message(chat_id, msg.message_id)  # FIXED #3
    except Exception as exc:
        logger.warning(f"Could not pin topic menu in topic {topic_id}: {exc}")  # FIXED #3
