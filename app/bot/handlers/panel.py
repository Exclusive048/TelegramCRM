from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger

from app.bot.ui.panel import (
    render_panel_home,
    render_panel_team,
    render_panel_team_add_prompt,
    build_kb_panel_home,
    build_kb_panel_team,
    build_kb_panel_team_add_prompt,
    parse_panel_callback,
)
from app.core.config import settings
from app.core.permissions import is_crm_admin
from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.telegram.safe_sender import TelegramSafeSender

router = Router()


class PanelAddManagerState(StatesGroup):
    waiting_for_contact = State()


async def _check_admin(bot: Bot, user_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        return await is_crm_admin(bot, repo, settings.crm_group_id, user_id)


async def _pin_panel_message(bot: Bot, chat_id: int, topic_id: int, message_id: int):
    try:
        await bot.unpin_all_forum_topic_messages(chat_id, message_thread_id=topic_id)
    except Exception as e:
        logger.warning(f"Could not unpin old panel messages: {e}")
    try:
        await bot.pin_chat_message(chat_id=chat_id, message_id=message_id, disable_notification=True)
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
            await _pin_panel_message(sender.bot, chat_id, topic_id, new_msg.message_id)
            logger.warning(f"Panel message restored with new message_id={new_msg.message_id}")
            return new_msg.message_id
        logger.error(f"Failed to edit panel message: {e}")
        return message_id


async def ensure_panel_message(sender: TelegramSafeSender, chat_id: int, topic_id: int) -> int:
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
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
            await _pin_panel_message(sender.bot, chat_id, topic_id, message_id)
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
        await _pin_panel_message(sender.bot, chat_id, topic_id, msg.message_id)
        return msg.message_id


@router.callback_query(F.data.startswith("panel:") | F.data.startswith("team:"))
async def handle_panel_actions(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender):
    action = parse_panel_callback(callback.data)
    if not action:
        await sender.answer(callback)
        return

    if not await _check_admin(callback.bot, callback.from_user.id):
        await sender.answer(callback, "⛔ Нет доступа.", show_alert=True)
        return

    await sender.answer(callback)

    chat_id = callback.message.chat.id
    topic_id = callback.message.message_thread_id or settings.topic_managers
    message_id = callback.message.message_id

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)

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
async def handle_manager_contact(message: Message, state: FSMContext, sender: TelegramSafeSender):
    if not await _check_admin(message.bot, message.from_user.id):
        return

    data = await state.get_data()
    panel_chat_id = data.get("panel_chat_id", settings.crm_group_id)
    panel_topic_id = data.get("panel_topic_id", settings.topic_managers)
    panel_message_id = data.get("panel_message_id")

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)

        if not message.contact or not message.contact.user_id:
            text = (
                "⚠️ <b>Нужен контакт пользователя Telegram</b>\n\n"
                "Пришлите контакт менеджера через кнопку «Поделиться контактом»."
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
            return

        contact = message.contact
        name = " ".join(filter(None, [contact.first_name, contact.last_name])) or "—"
        username = None
        try:
            chat = await sender.bot.get_chat(contact.user_id)
            if chat.full_name:
                name = chat.full_name
            username = chat.username
        except Exception as e:
            logger.warning(f"Could not fetch chat info for {contact.user_id}: {e}")

        manager = await repo.upsert_manager_from_contact(
            tg_id=contact.user_id,
            name=name,
            username=username,
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
