from aiogram import Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, ChatMemberOwner
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from loguru import logger

from app.core.config import settings
from app.core.permissions import is_tg_admin
from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.db.models.lead import ManagerRole, LeadStatus
from app.bot.handlers.panel import ensure_panel_message
from app.telegram.safe_sender import TelegramSafeSender

router = Router()

TOPICS_TO_CREATE = [
    ("📥 Лиды", "TOPIC_NEW"),
    ("🛠 В работе", "TOPIC_IN_PROGRESS"),
    ("💳 Оплачено", "TOPIC_PAID"),
    ("🏆 Успех", "TOPIC_SUCCESS"),
    ("❌ Отклонено", "TOPIC_REJECTED"),
    ("💬 Общий чат", "TOPIC_GENERAL"),
    ("🔔 Напоминания", "TOPIC_REMINDERS"),
    ("🗂 Кабинет", "TOPIC_CABINET"),
    ("👥 Чат менеджеров", "TOPIC_MANAGERS"),
    ("📚 База знаний", "TOPIC_KNOWLEDGE"),
]


async def _ensure_owner_registered(bot: Bot, user_id: int, full_name: str, username: str | None):
    """
    Если владелец группы ещё не в БД — авторегистрируем его как ADMIN.
    Вызывается при первом /setup чтобы не нужен был отдельный скрипт.
    """
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


# ── /setup ────────────────────────────────────────────

@router.message(Command("setup"))
async def cmd_setup(message: Message, bot: Bot, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        await sender.answer(message, "⚠️ Команда работает только внутри CRM-группы.")
        return

    # Только TG-администраторы и владелец
    if not await is_tg_admin(bot, settings.crm_group_id, message.from_user.id):
        await sender.answer(message, "⛔️ Только администраторы группы могут использовать /setup.")
        return

    # Авторегистрация владельца — убирает необходимость в отдельном скрипте
    member = await bot.get_chat_member(settings.crm_group_id, message.from_user.id)
    if isinstance(member, ChatMemberOwner):
        await _ensure_owner_registered(
            bot,
            message.from_user.id,
            message.from_user.full_name,
            message.from_user.username,
        )

    progress = await sender.answer(message, "⚙️ Создаю топики...")
    created, errors = [], []

    for name, env_key in TOPICS_TO_CREATE:
        try:
            topic = await bot.create_forum_topic(chat_id=settings.crm_group_id, name=name)
            created.append((name, env_key, topic.message_thread_id))
            logger.info(f"Topic created: {name} → id={topic.message_thread_id}")
        except Exception as e:
            errors.append((name, str(e)))
            logger.error(f"Failed to create topic '{name}': {e}")

    topic_managers_id = next(
        (tid for _, env_key, tid in created if env_key == "TOPIC_MANAGERS"),
        settings.topic_managers,
    )
    try:
        await ensure_panel_message(sender, settings.crm_group_id, topic_managers_id)
        logger.info(f"Panel message ensured in topic {topic_managers_id}")
    except Exception as e:
        errors.append(("Пульт управления", str(e)))
        logger.error(f"Failed to ensure panel message: {e}")

    lines = ["<b>✅ Топики созданы!</b>\n", "Вставь в <code>.env</code>:\n<code>"]
    for _, env_key, tid in created:
        lines.append(f"{env_key}={tid}")
    lines.append("</code>")

    if errors:
        lines.append("\n<b>⚠️ Ошибки:</b>")
        for name, err in errors:
            lines.append(f"• {name}: {err}")

    lines.append("\n<i>После обновления .env перезапусти: <code>python main.py</code></i>")
    await sender.edit_message_text(
        chat_id=progress.chat.id,
        message_id=progress.message_id,
        text="\n".join(lines),
        parse_mode="HTML",
        thread_id=progress.message_thread_id,
    )

    # Меню топика и кнопки
    try:
        await _ensure_topic_menus(sender, created)
        logger.info("Topic menus ensured")
    except Exception as e:
        logger.error(f"Failed to ensure topic menus: {e}")



# ── /add_manager ──────────────────────────────────────

@router.message(Command("add_manager"))
async def cmd_add_manager(message: Message, bot: Bot, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        return

    if not await is_tg_admin(bot, settings.crm_group_id, message.from_user.id):
        await sender.answer(message, "⛔️ Только администраторы группы могут назначать менеджеров.")
        return

    if not message.reply_to_message:
        await sender.answer(
            message,
            "ℹ️ Ответьте на любое сообщение участника командой /add_manager\n\n"
            "<i>Нет сообщения? Попросите человека написать что-нибудь в группу.</i>",
            parse_mode="HTML",
        )
        return

    target = message.reply_to_message.from_user

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        existing = await repo.get_manager_by_tg_id(target.id)

        if existing:
            if existing.is_active:
                await sender.answer(message, f"ℹ️ {target.full_name} уже является менеджером.")
            else:
                existing.is_active = True
                await session.commit()
                await sender.answer(message, f"✅ {target.full_name} восстановлен как менеджер.")
            return

        await repo.create_manager(
            tg_id=target.id,
            name=target.full_name,
            username=target.username,
            role=ManagerRole.MANAGER,
        )
        await session.commit()

    await sender.answer(
        message,
        f"✅ <b>{target.full_name}</b> назначен менеджером!\n"
        f"Username: @{target.username or '—'}\n\n"
        f"Чтобы дать права администратора — ответьте на его сообщение: /make_admin",
        parse_mode="HTML",
    )


# ── /make_admin ───────────────────────────────────────

@router.message(Command("make_admin"))
async def cmd_make_admin(message: Message, bot: Bot, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        return

    member = await bot.get_chat_member(settings.crm_group_id, message.from_user.id)
    if not isinstance(member, ChatMemberOwner):
        await sender.answer(message, "⛔️ Только владелец группы может назначать CRM-администраторов.")
        return

    if not message.reply_to_message:
        await sender.answer(message, "ℹ️ Ответьте на сообщение пользователя командой /make_admin")
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

    await sender.answer(
        message,
        f"👑 <b>{target.full_name}</b> теперь CRM-администратор.\n"
        f"Может назначать менеджеров, делать выгрузки и смотреть статистику.",
        parse_mode="HTML",
    )


# ── /remove_manager ───────────────────────────────────

@router.message(Command("remove_manager"))
async def cmd_remove_manager(message: Message, bot: Bot, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        return

    if not await is_tg_admin(bot, settings.crm_group_id, message.from_user.id):
        await sender.answer(message, "⛔️ Только администраторы группы могут убирать менеджеров.")
        return

    if not message.reply_to_message:
        await sender.answer(message, "ℹ️ Ответьте на сообщение пользователя командой /remove_manager")
        return

    target = message.reply_to_message.from_user

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        ok = await repo.deactivate_manager(target.id)
        await session.commit()

    if ok:
        await sender.answer(message, f"✅ {target.full_name} удалён из менеджеров.")
    else:
        await sender.answer(message, f"ℹ️ {target.full_name} не найден в списке менеджеров.")


# ── /managers ─────────────────────────────────────────

@router.message(Command("managers"))
async def cmd_managers(message: Message, bot: Bot, sender: TelegramSafeSender):
    if message.chat.id != settings.crm_group_id:
        return

    if not await is_tg_admin(bot, settings.crm_group_id, message.from_user.id):
        await sender.answer(message, "⛔️ Только администраторы могут просматривать список.")
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        managers = await repo.get_all_managers()

    if not managers:
        await sender.answer(message, "👥 Менеджеров пока нет.\nДобавь через /add_manager (ответом на сообщение)")
        return

    lines = ["<b>👥 Команда:</b>\n"]
    for m in managers:
        icon = "👑" if m.is_admin else "👤"
        role = "Администратор" if m.is_admin else "Менеджер"
        username = f"@{m.tg_username}" if m.tg_username else "—"
        lines.append(f"{icon} <b>{m.name}</b> ({role})  {username}")

    lines.append(
        "\n<i>/add_manager — добавить (ответом на сообщение)\n"
        "/make_admin — дать права администратора\n"
        "/remove_manager — убрать</i>"
    )
    await sender.answer(message, "\n".join(lines), parse_mode="HTML")


async def _ensure_topic_menus(sender: TelegramSafeSender, created: list[tuple[str, str, int]]):
    topic_map = {env_key: tid for _, env_key, tid in created}

    topic_new = topic_map.get("TOPIC_NEW", settings.topic_new)
    topic_in_progress = topic_map.get("TOPIC_IN_PROGRESS", settings.topic_in_progress)
    topic_paid = topic_map.get("TOPIC_PAID", settings.topic_paid)
    topic_success = topic_map.get("TOPIC_SUCCESS", settings.topic_success)
    topic_rejected = topic_map.get("TOPIC_REJECTED", settings.topic_rejected)

    await _post_topic_menu(sender, topic_new, LeadStatus.NEW)
    await _post_topic_menu(sender, topic_in_progress, LeadStatus.IN_PROGRESS)
    await _post_topic_menu(sender, topic_paid, LeadStatus.PAID)
    await _post_topic_menu(sender, topic_success, LeadStatus.SUCCESS)
    await _post_topic_menu(sender, topic_rejected, LeadStatus.REJECTED)


def _build_topic_menu(status: LeadStatus) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    if status == LeadStatus.NEW:
        builder.row(
            InlineKeyboardButton(
                text="\u2795 \u0421\u043e\u0437\u0434\u0430\u0442\u044c \u0437\u0430\u044f\u0432\u043a\u0443",
                callback_data="menu:create",
            ),
            InlineKeyboardButton(
                text="\U0001f4c5 \u0412\u044b\u0431\u0440\u0430\u0442\u044c \u043f\u0435\u0440\u0438\u043e\u0434",
                callback_data="menu:period:new",
            ),
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text="\U0001f4c5 \u0412\u044b\u0431\u0440\u0430\u0442\u044c \u043f\u0435\u0440\u0438\u043e\u0434",
                callback_data=f"menu:period:{status.value}",
            ),
        )
    return builder


async def _post_topic_menu(sender: TelegramSafeSender, topic_id: int, status: LeadStatus):
    text = "\u041d\u0438\u0436\u043d\u0435\u0435 \u043c\u0435\u043d\u044e"
    builder = _build_topic_menu(status)
    msg = await sender.send_message(
        chat_id=settings.crm_group_id,
        message_thread_id=topic_id,
        text=text,
        reply_markup=builder.as_markup(),
    )
    try:
        await sender.bot.unpin_all_forum_topic_messages(settings.crm_group_id, message_thread_id=topic_id)
    except Exception:
        pass
    try:
        await sender.bot.pin_chat_message(chat_id=settings.crm_group_id, message_id=msg.message_id, disable_notification=True)
    except Exception:
        pass
