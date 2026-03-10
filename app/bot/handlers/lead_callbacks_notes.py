from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.constants.ttl import TTL_ERROR_SEC, TTL_MENU_SEC
from app.bot.keyboards.lead_keyboards import make_reminder_keyboard
from app.bot.ui.message_ref import MessageRef
from app.bot.utils.force_reply import cleanup_force_reply, reject_non_force_reply, start_force_reply
from app.bot.utils.menu_cleanup import cleanup_inline_menu, cleanup_inline_menu_by_id
from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.services.lead_service import LeadService
from app.services.reminder_service import ReminderService
from app.telegram.safe_sender import TelegramSafeSender

from .lead_callbacks_shared import (
    NO_ACCESS_TEXT,
    NoteState,
    ReminderState,
    _get_group_id,
    _get_manager,
    _manager_can_act,
    _parse_custom_datetime,
)

router = Router()


def _resolve_quick_reminder(choice: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    if choice == "1h":
        return now + timedelta(hours=1)
    if choice == "3h":
        return now + timedelta(hours=3)
    if choice == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return None


def _format_reminder_dt(remind_at: datetime) -> str:
    return remind_at.strftime("%d.%m в %H:%M")


def _replace_confirm_keyboard(lead_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Заменить",
            callback_data=f"lead:remind_replace:{lead_id}:confirm",
        ),
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"lead:remind_replace:{lead_id}:cancel",
        ),
    )
    return builder.as_markup()


@router.callback_query(F.data.regexp(r"^lead:(note|remind|remind_set):"))
async def handle_lead_note_action(
    callback: CallbackQuery,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await sender.answer(callback)
        return

    action = parts[1]
    lead_id_raw = parts[2]
    if not lead_id_raw.isdigit():
        await sender.answer(callback)
        return
    lead_id = int(lead_id_raw)
    source_ref = MessageRef.from_callback(callback)
    group_id = _get_group_id(tenant) or (callback.message.chat.id if callback.message.chat.id < 0 else None)
    if not group_id and callback.message:
        group_id = callback.message.chat.id
    tenant_id = tenant.id if tenant else None

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, callback.from_user.id, tenant_id=tenant_id)
        if not manager:
            await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
            return

        if action == "note":
            lead_for_access = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            await state.set_state(NoteState.waiting_for_text)
            await state.update_data(lead_id=lead_id, message_ref=source_ref.to_dict() if source_ref else None)
            await sender.answer(callback)
            await start_force_reply(
                callback,
                state,
                sender,
                "ℹ️ Введите заметку:",
                lead_id=lead_id,
            )
            return

        if action == "remind":
            lead_for_access = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            await sender.answer(callback)
            menu_msg = await sender.send_ephemeral_text(
                chat_id=callback.message.chat.id,
                message_thread_id=callback.message.message_thread_id,
                text="ℹ️ Когда напомнить?",
                reply_markup=make_reminder_keyboard(lead_id),
                ttl_sec=TTL_MENU_SEC,
            )
            await state.update_data(
                reminder_menu_chat_id=menu_msg.chat.id,
                reminder_menu_id=menu_msg.message_id,
                reminder_menu_thread_id=menu_msg.message_thread_id,
            )
            return

        if action == "remind_set":
            if len(parts) < 4:
                await sender.answer(callback)
                return
            lead_for_access = await repo.get_by_id(lead_id, tenant_id=tenant_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return

            choice = parts[3]
            if choice == "custom":
                await state.set_state(ReminderState.waiting_for_custom_time)
                await state.update_data(
                    lead_id=lead_id,
                    reminder_menu_chat_id=callback.message.chat.id if callback.message else None,
                    reminder_menu_id=callback.message.message_id if callback.message else None,
                    reminder_menu_thread_id=callback.message.message_thread_id if callback.message else None,
                )
                await sender.answer(callback)
                await cleanup_inline_menu(callback, sender)
                await start_force_reply(
                    callback,
                    state,
                    sender,
                    "ℹ️ Введите дату и время (ДД.ММ.ГГГГ ЧЧ:ММ):",
                    lead_id=lead_id,
                )
                return

            remind_at = _resolve_quick_reminder(choice)
            if not remind_at:
                await sender.answer(callback)
                return

            existing = await repo.get_active_reminder_for_manager(lead_id, callback.from_user.id)
            if existing:
                await state.update_data(
                    reminder_replace_lead_id=lead_id,
                    reminder_replace_manager_tg_id=callback.from_user.id,
                    reminder_replace_at=remind_at.isoformat(),
                    reminder_replace_group_id=group_id,
                )
                await sender.answer(callback)
                await sender.edit_message_text(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    text=(
                        f"У заявки уже есть напоминание на {_format_reminder_dt(existing.remind_at)}.\n"
                        "Заменить?"
                    ),
                    reply_markup=_replace_confirm_keyboard(lead_id),
                    thread_id=callback.message.message_thread_id,
                )
                return

            reminder = await ReminderService(repo, sender).schedule_reminder(
                lead_id=lead_id,
                manager_tg_id=callback.from_user.id,
                remind_at=remind_at,
                group_id=group_id,
            )
            await session.commit()
            if reminder:
                await sender.answer(callback, "✅ Готово.")
                await cleanup_inline_menu(callback, sender)
            else:
                await sender.answer(callback, "⚠️ Не удалось поставить напоминание.", show_alert=True)
            return


@router.callback_query(F.data.startswith("lead:remind_replace:"))
async def handle_reminder_replace_confirmation(
    callback: CallbackQuery,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    parts = callback.data.split(":")
    if len(parts) < 4 or not parts[2].isdigit():
        await sender.answer(callback)
        return

    lead_id = int(parts[2])
    decision = parts[3]
    if decision == "cancel":
        await state.clear()
        await sender.answer(callback, "Отменено.")
        await cleanup_inline_menu(callback, sender, done_text="Действие отменено.")
        return

    data = await state.get_data()
    stored_lead_id = data.get("reminder_replace_lead_id")
    remind_at_raw = data.get("reminder_replace_at")
    manager_tg_id = data.get("reminder_replace_manager_tg_id")
    group_id = data.get("reminder_replace_group_id")
    tenant_id = tenant.id if tenant else None

    if stored_lead_id != lead_id or not remind_at_raw:
        await state.clear()
        await sender.answer(callback, "⚠️ Данные напоминания устарели.", show_alert=True)
        return

    try:
        remind_at = datetime.fromisoformat(remind_at_raw)
    except ValueError:
        await state.clear()
        await sender.answer(callback, "⚠️ Некорректная дата напоминания.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, callback.from_user.id, tenant_id=tenant_id)
        if not manager:
            await state.clear()
            await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
            return
        lead_obj = await repo.get_by_id(lead_id, tenant_id=tenant_id)
        if not lead_obj or not _manager_can_act(manager, lead_obj):
            await state.clear()
            await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
            return

        reminder_service = ReminderService(repo, sender)
        reminder, _ = await reminder_service.replace_reminder(
            lead_id=lead_id,
            manager_tg_id=manager_tg_id or callback.from_user.id,
            remind_at=remind_at,
            group_id=group_id,
        )
        await session.commit()

    await state.clear()
    if reminder:
        await sender.answer(callback, "✅ Напоминание обновлено.")
        await cleanup_inline_menu(callback, sender)
    else:
        await sender.answer(callback, "⚠️ Не удалось обновить напоминание.", show_alert=True)


@router.message(NoteState.waiting_for_text)
async def handle_note_text(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    group_id = _get_group_id(tenant) or (message.chat.id if message.chat.id < 0 else None)
    tenant_id = tenant.id if tenant else None

    if not await reject_non_force_reply(message, state, sender):
        return

    text = (message.text or "").strip()
    if not text:
        try:
            await sender.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                thread_id=message.message_thread_id,
            )
        except Exception:
            pass
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="ℹ️ Введите текст заметки.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    data = await state.get_data()
    lead_id = data.get("lead_id")
    target_ref = MessageRef.from_dict(data.get("message_ref"))
    if not lead_id:
        await cleanup_force_reply(sender, state, message)
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, message.from_user.id, tenant_id=tenant_id)
        if not manager:
            await cleanup_force_reply(sender, state, message)
            await state.clear()
            return
        lead_obj = await repo.get_by_id(int(lead_id), tenant_id=tenant_id)
        if not lead_obj or not _manager_can_act(manager, lead_obj):
            await cleanup_force_reply(sender, state, message)
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text=NO_ACCESS_TEXT,
                ttl_sec=TTL_ERROR_SEC,
            )
            await state.clear()
            return

        service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
        await service.add_comment(
            lead_id=int(lead_id),
            text=text,
            author=message.from_user.full_name,
            target_ref=target_ref,
        )
        await session.commit()

    await cleanup_force_reply(sender, state, message)
    await state.clear()


@router.message(ReminderState.waiting_for_custom_time)
async def handle_custom_reminder_time(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    group_id = _get_group_id(tenant) or (message.chat.id if message.chat.id < 0 else None)
    tenant_id = tenant.id if tenant else None

    if not await reject_non_force_reply(message, state, sender):
        return

    remind_at = _parse_custom_datetime(message.text or "")
    if not remind_at:
        try:
            await sender.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                thread_id=message.message_thread_id,
            )
        except Exception:
            pass
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Некорректный формат. Пример: 28.02.2026 14:30.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    data = await state.get_data()
    lead_id = data.get("lead_id")
    menu_chat_id = data.get("reminder_menu_chat_id")
    menu_message_id = data.get("reminder_menu_id")
    menu_thread_id = data.get("reminder_menu_thread_id")
    if not lead_id:
        await cleanup_force_reply(sender, state, message)
        await state.clear()
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, message.from_user.id, tenant_id=tenant_id)
        if not manager:
            await cleanup_force_reply(sender, state, message)
            await state.clear()
            return
        lead_obj = await repo.get_by_id(int(lead_id), tenant_id=tenant_id)
        if not lead_obj or not _manager_can_act(manager, lead_obj):
            await cleanup_force_reply(sender, state, message)
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text=NO_ACCESS_TEXT,
                ttl_sec=TTL_ERROR_SEC,
            )
            await state.clear()
            return

        existing = await repo.get_active_reminder_for_manager(int(lead_id), message.from_user.id)
        if existing:
            await state.update_data(
                reminder_replace_lead_id=int(lead_id),
                reminder_replace_manager_tg_id=message.from_user.id,
                reminder_replace_at=remind_at.isoformat(),
                reminder_replace_group_id=group_id,
            )
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text=(
                    f"У заявки уже есть напоминание на {_format_reminder_dt(existing.remind_at)}.\n"
                    "Заменить?"
                ),
                reply_markup=_replace_confirm_keyboard(int(lead_id)),
                ttl_sec=TTL_MENU_SEC,
            )
            await cleanup_force_reply(sender, state, message)
            await state.set_state(default_state)
            return

        reminder = await ReminderService(repo, sender).schedule_reminder(
            lead_id=int(lead_id),
            manager_tg_id=message.from_user.id,
            remind_at=remind_at,
            group_id=group_id,
        )
        await session.commit()
        if reminder:
            await cleanup_inline_menu_by_id(
                sender,
                chat_id=menu_chat_id,
                message_id=menu_message_id,
                thread_id=menu_thread_id,
            )
        else:
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="⚠️ Не удалось поставить напоминание.",
                ttl_sec=TTL_ERROR_SEC,
            )

    await cleanup_force_reply(sender, state, message)
    await state.clear()


@router.message(F.reply_to_message, ~F.text.startswith("/"), StateFilter(default_state))
async def handle_reply_note(message: Message, sender: TelegramSafeSender, tenant=None):
    group_id = _get_group_id(tenant) or (message.chat.id if message.chat.id < 0 else None)
    tenant_id = tenant.id if tenant else None
    if not group_id or message.chat.id != group_id:
        return
    if not message.text or message.text.startswith("/"):
        return

    target_ref = MessageRef.from_reply(message.reply_to_message)
    if not target_ref:
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, message.from_user.id, tenant_id=tenant_id)
        if not manager:
            try:
                await sender.delete_message(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    thread_id=message.message_thread_id,
                )
            except Exception:
                pass
            return

        record = await repo.get_card_message(target_ref.chat_id, target_ref.message_id, tenant_id=tenant_id)
        lead_id = record.lead_id if record else None
        if not lead_id:
            lead = await repo.get_lead_by_tg_message(target_ref.message_id, target_ref.topic_id)
            if lead and (tenant_id is None or lead.tenant_id == tenant_id):
                await repo.ensure_active_card_message(
                    lead_id=lead.id,
                    chat_id=target_ref.chat_id,
                    topic_id=target_ref.topic_id,
                    message_id=target_ref.message_id,
                )
                lead_id = lead.id

        if not lead_id:
            return

        lead_obj = await repo.get_by_id(lead_id, tenant_id=tenant_id)
        if not lead_obj or not _manager_can_act(manager, lead_obj):
            try:
                await sender.delete_message(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    thread_id=message.message_thread_id,
                )
            except Exception:
                pass
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text=NO_ACCESS_TEXT,
                ttl_sec=TTL_ERROR_SEC,
            )
            return

        service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
        await service.add_comment(
            lead_id=lead_id,
            text=message.text.strip(),
            author=message.from_user.full_name,
            target_ref=target_ref,
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
