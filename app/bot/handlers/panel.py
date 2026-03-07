from aiogram import Router, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger

from app.bot.topic_resolver import resolve_topic_thread_id
from app.bot.topic_cache import invalidate as invalidate_topic_cache
from app.bot.topics import TOPIC_SPECS, TopicKey
from app.bot.ui.panel import (
    render_panel_home,
    render_panel_team,
    render_panel_team_add_prompt,
    build_kb_panel_home,
    build_kb_panel_team,
    build_kb_panel_team_add_prompt,
    parse_panel_callback,
)
from app.bot.utils.handler_helpers import resolve_admin_context
from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.db.repositories.tenant_topics import TenantTopicRepository
from app.telegram.safe_sender import TelegramSafeSender

router = Router()


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


class PanelAddManagerState(StatesGroup):
    waiting_for_contact = State()


async def _pin_panel_message(sender: TelegramSafeSender, chat_id: int, topic_id: int, message_id: int):
    try:
        await sender.unpin_all_forum_topic_messages(chat_id, topic_id)
    except Exception as e:
        logger.warning(f"Could not unpin old panel messages: {e}")
    try:
        await sender.pin_chat_message(chat_id, message_id, disable_notification=True)
    except Exception as e:
        logger.warning(f"Could not pin panel message: {e}")


async def _safe_edit_panel_message(
    sender: TelegramSafeSender,
    repo: LeadRepository,
    chat_id: int,
    topic_id: int,
    message_id: int,
    text: str,
    reply_markup,
) -> int:
    try:
        await sender.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            thread_id=topic_id,
        )
        return message_id
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            try:
                await sender.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=reply_markup,
                    thread_id=topic_id,
                )
            except TelegramBadRequest as edit_err:
                logger.warning(f"Panel reply markup edit failed: {edit_err}")
            return message_id
        if any(token in msg for token in ("message to edit not found", "message_id_invalid", "message can't be edited")):
            new_msg = await sender.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            await repo.set_panel_message_id(chat_id, topic_id, new_msg.message_id)
            await _pin_panel_message(sender, chat_id, topic_id, new_msg.message_id)
            logger.warning(f"Panel message restored with new message_id={new_msg.message_id}")
            return new_msg.message_id
        logger.error(f"Failed to edit panel message: {e}")
        return message_id


async def ensure_panel_message(sender: TelegramSafeSender, chat_id: int, topic_id: int) -> int | None:
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        topic_repo = TenantTopicRepository(session)
        try:
            try:
                await _probe_topic_thread(sender, chat_id, topic_id)
            except TelegramBadRequest as e:
                if "message thread not found" in str(e).lower():
                    topic_id = None
                else:
                    raise
        except Exception as exc:
            logger.error(f"Failed to validate panel topic: {exc}")
            return None

        if not topic_id:
            spec = _get_topic_spec(TopicKey.MANAGERS)
            if not spec:
                logger.error("Topic spec missing for MANAGERS")
                return None
            try:
                topic = await sender.create_forum_topic(chat_id, spec.title)
                topic_id = topic.message_thread_id
                await topic_repo.upsert_topic(
                    chat_id=chat_id,
                    key=TopicKey.MANAGERS.value,
                    thread_id=topic_id,
                    title=spec.title,
                )
                await session.commit()
                invalidate_topic_cache(chat_id)
                logger.info(f"Managers topic recreated -> id={topic_id}")
            except Exception as exc:
                logger.error(f"Failed to recreate managers topic: {exc}")
                return None

        existing_message_id = await repo.get_or_create_panel_message_id(chat_id, topic_id)
        text = render_panel_home()
        keyboard = build_kb_panel_home()

        if existing_message_id:
            message_id = await _safe_edit_panel_message(
                sender,
                repo,
                chat_id,
                topic_id,
                existing_message_id,
                text,
                keyboard,
            )
            await repo.set_panel_message_id(chat_id, topic_id, message_id)
            await session.commit()
            await _pin_panel_message(sender, chat_id, topic_id, message_id)
            return message_id

        msg = await sender.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await repo.get_or_create_panel_message_id(chat_id, topic_id, msg.message_id)
        await session.commit()
        await _pin_panel_message(sender, chat_id, topic_id, msg.message_id)
        return msg.message_id


@router.message(Command("panel"))  # FIXED #11
async def cmd_panel(message: Message, sender: TelegramSafeSender, tenant=None):
    """Восстанавливает пульт управления командой в текущем топике."""  # FIXED #11
    ctx = await resolve_admin_context(message, tenant, sender)
    if not ctx:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Только CRM-администраторы могут использовать /panel.",
            ttl_sec=30,
        )
        return

    topic_id = message.message_thread_id
    if not topic_id:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=None,
            text="⚠️ Команда /panel работает только внутри топика Менеджеров.",
            ttl_sec=30,
        )
        return

    await ensure_panel_message(sender, message.chat.id, topic_id)  # FIXED #11
    try:
        await sender.delete_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            thread_id=message.message_thread_id,
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("panel:") | F.data.startswith("team:"))
async def handle_panel_actions(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender, tenant=None):
    action = parse_panel_callback(callback.data)
    if not action:
        await sender.answer(callback)
        return

    ctx = await resolve_admin_context(callback, tenant, sender)
    if not ctx:
        await sender.answer(callback, "⛔ Нет доступа.", show_alert=True)
        return
    group_id, _ = ctx

    await sender.answer(callback)

    chat_id = callback.message.chat.id
    message_id = callback.message.message_id

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        topic_id = await resolve_topic_thread_id(
            group_id,
            TopicKey.MANAGERS,
            session,
            sender=None,
        ) or callback.message.message_thread_id  # FIXED #1
        if not topic_id:
            return

        if action in {"panel_home", "panel_team", "team_cancel"}:
            await state.clear()

        if action == "panel_home":
            text = render_panel_home()
            keyboard = build_kb_panel_home()
        elif action == "panel_team":
            managers = await repo.get_all_managers(include_inactive=True)
            text = render_panel_team(managers)
            keyboard = build_kb_panel_team(managers)
        elif action == "team_add":
            await state.set_state(PanelAddManagerState.waiting_for_contact)
            await state.update_data(
                panel_message_id=message_id,
                panel_topic_id=topic_id,
                panel_chat_id=chat_id,
            )
            text = render_panel_team_add_prompt()
            keyboard = build_kb_panel_team_add_prompt()
        elif action == "team_cancel":
            managers = await repo.get_all_managers(include_inactive=True)
            text = render_panel_team(managers)
            keyboard = build_kb_panel_team(managers)
        else:
            await sender.answer(callback)
            return

        new_message_id = await _safe_edit_panel_message(
            sender,
            repo,
            chat_id,
            topic_id,
            message_id,
            text,
            keyboard,
        )
        await repo.set_panel_message_id(chat_id, topic_id, new_message_id)
        await session.commit()

        if action == "team_add":
            await state.update_data(panel_message_id=new_message_id)


@router.message(PanelAddManagerState.waiting_for_contact)
async def handle_manager_contact(message: Message, state: FSMContext, sender: TelegramSafeSender, tenant=None):
    ctx = await resolve_admin_context(message, tenant, sender)
    if not ctx:
        return
    group_id, _ = ctx

    data = await state.get_data()
    panel_chat_id = data.get("panel_chat_id", group_id)
    panel_topic_id = data.get("panel_topic_id")
    panel_message_id = data.get("panel_message_id")

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        if not panel_topic_id:
            panel_topic_id = await resolve_topic_thread_id(
                panel_chat_id,
                TopicKey.MANAGERS,
                session,
                sender=sender,
                thread_id=None,
            )
            if not panel_topic_id:
                return

        # Принимаем контакт ИЛИ пересланное сообщение
        tg_id = None
        name = None
        username = None

        if message.contact and message.contact.user_id:
            # Вариант 1: кнопка "Поделиться контактом"
            tg_id = message.contact.user_id
            name = " ".join(filter(None, [message.contact.first_name, message.contact.last_name])) or "—"
            try:
                chat = await sender.get_chat(tg_id)
                if chat.full_name:
                    name = chat.full_name
                username = chat.username
            except Exception as e:
                logger.warning(f"Could not fetch chat info for {tg_id}: {e}")

        elif message.forward_origin:
            # Вариант 2: пересланное сообщение от пользователя
            origin = message.forward_origin
            if hasattr(origin, "sender_user") and origin.sender_user:
                user = origin.sender_user
                tg_id = user.id
                name = " ".join(filter(None, [user.first_name, user.last_name])) or "—"
                username = user.username
            elif hasattr(origin, "sender_user_name"):
                # Скрытый пользователь — нет tg_id
                pass

        if not tg_id:
            text = (
                "⚠️ <b>Как добавить менеджера:</b>\n\n"
                "1️⃣ Перешлите любое сообщение от менеджера\n"
                "2️⃣ Или поделитесь его контактом\n\n"
                "Менеджер должен разрешить пересылку сообщений."
            )
            keyboard = build_kb_panel_team_add_prompt()
            if panel_message_id:
                await _safe_edit_panel_message(
                    sender,
                    repo,
                    panel_chat_id,
                    panel_topic_id,
                    panel_message_id,
                    text,
                    keyboard,
                )
                await session.commit()
            try:
                await sender.delete_message(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    thread_id=message.message_thread_id,
                )
            except Exception:
                pass
            return

        manager = await repo.upsert_manager_from_contact(
            tg_id=tg_id,
            name=name,
            username=username,
            tenant_id=tenant.id if tenant else None,
        )
        await session.commit()
        logger.info(f"Manager upserted from contact: {manager.name} (tg_id={manager.tg_id})")

        managers = await repo.get_all_managers(include_inactive=True)
        text = render_panel_team(managers)
        keyboard = build_kb_panel_team(managers)

        if not panel_message_id:
            panel_message_id = await repo.get_or_create_panel_message_id(panel_chat_id, panel_topic_id)
            if not panel_message_id:
                panel_message_id = await ensure_panel_message(
                    sender,
                    panel_chat_id,
                    panel_topic_id,
                )

        if panel_message_id:
            new_message_id = await _safe_edit_panel_message(
                sender,
                repo,
                panel_chat_id,
                panel_topic_id,
                panel_message_id,
                text,
                keyboard,
            )
            await repo.set_panel_message_id(panel_chat_id, panel_topic_id, new_message_id)
            await session.commit()

    await state.clear()
    try:
        await sender.delete_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            thread_id=message.message_thread_id,
        )
    except Exception:
        pass

