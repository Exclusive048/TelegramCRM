from __future__ import annotations

from datetime import datetime, timedelta, timezone
import email

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from app.bot.constants.ttl import TTL_MENU_SEC, TTL_ERROR_SEC
from app.bot.utils.force_reply import (
    start_force_reply,
    reject_non_force_reply,
    cleanup_force_reply,
    delete_force_reply_prompt,
)
from app.bot.keyboards.lead_keyboards import make_reject_reason_keyboard, make_reminder_keyboard
from app.bot.utils.menu_cleanup import cleanup_inline_menu, cleanup_inline_menu_by_id
from app.bot.ui.message_ref import MessageRef
from app.db.database import AsyncSessionLocal
from app.db.models.lead import LeadStatus
from app.db.repositories.lead_repository import LeadRepository
from app.services.lead_service import LeadService
from app.services.reminder_service import ReminderService
from app.telegram.safe_sender import TelegramSafeSender

router = Router()
import re
PHONE_RE = re.compile(r'^[+7890][0-9\-\s]{6,19}$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

class NoteState(StatesGroup):
    waiting_for_text = State()


class RejectState(StatesGroup):
    waiting_for_reason = State()
    waiting_for_custom_reason = State()


class AmountState(StatesGroup):
    waiting_for_amount = State()


class ReminderState(StatesGroup):
    waiting_for_custom_time = State()


class CreateLeadState(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_email = State()
    waiting_for_service = State()
    waiting_for_comment = State()


REJECT_REASON_LABELS = {
    "no_budget": "Нет бюджета",
    "no_answer": "Не дозвонились",
    "not_target": "Не целевой",
    "changed_mind": "Передумал",
}
NO_ACCESS_TEXT = "\u26d4\ufe0f \u0423 \u0432\u0430\u0441 \u043d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u043a \u044d\u0442\u043e\u043c\u0443 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u044e."


def _get_group_id(tenant) -> int | None:
    return tenant.group_id if tenant else None


def _parse_amount(value: str) -> float | None:
    normalized = value.strip().replace(" ", "").replace(",", ".")
    try:
        amount = float(normalized)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return amount


def _parse_custom_datetime(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m %H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%d.%m %H:%M":
                parsed = parsed.replace(year=datetime.now(timezone.utc).year)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def _get_manager(repo: LeadRepository, user_id: int):
    return await repo.get_manager_by_tg_id(user_id)


def _manager_can_act(manager, lead) -> bool:
    if manager.is_admin:
        return True
    if lead.manager_id is None:
        return True
    return lead.manager_id == manager.id


@router.callback_query(F.data.startswith("lead:"))
async def handle_lead_action(
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
    group_id = _get_group_id(tenant)
    if not group_id and callback.message:
        group_id = callback.message.chat.id

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, callback.from_user.id)
        if not manager:
            await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
            return

        service = LeadService(repo, sender, group_id=group_id)

        if action == "take":
            lead = await service.take_in_progress(lead_id, callback.from_user.id, source_ref)
            if lead:
                await session.commit()
                await sender.answer(callback, "✅ Заявка взята в работу")
                return

            existing = await repo.get_by_id(lead_id)
            if existing and existing.status != LeadStatus.NEW:
                other_name = existing.manager.name if existing.manager else "\u0434\u0440\u0443\u0433\u043e\u0439 \u043c\u0435\u043d\u0435\u0434\u0436\u0435\u0440"
                if existing.manager and existing.manager.tg_id == callback.from_user.id:
                    await sender.answer(callback, "Вы уже взяли эту заявку.", show_alert=True)
                else:
                    await sender.answer(callback, f"Уже взято: {other_name}", show_alert=True)
                return

            await sender.answer(callback, "Уже взято другим менеджером.", show_alert=True)
            return

        if action == "paid":
            lead_for_access = await repo.get_by_id(lead_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            await state.set_state(AmountState.waiting_for_amount)
            await state.update_data(lead_id=lead_id, message_ref=source_ref.to_dict() if source_ref else None)
            await sender.answer(callback)
            await start_force_reply(callback, state, sender, "Введите сумму сделки (руб.):")
            return

        if action == "success":
            lead_for_access = await repo.get_by_id(lead_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            lead = await service.mark_success(lead_id, callback.from_user.id, source_ref)
            if lead:
                await session.commit()
                await sender.answer(callback, "🏆 Успешно завершено")
                return
            await sender.answer(callback, "⚠️ Не удалось завершить сделку.", show_alert=True)
            return

        if action == "reject":
            lead_for_access = await repo.get_by_id(lead_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            await state.set_state(RejectState.waiting_for_reason)
            await state.update_data(lead_id=lead_id, message_ref=source_ref.to_dict() if source_ref else None)
            await sender.answer(callback)
            await sender.send_ephemeral_text(
                chat_id=callback.message.chat.id,
                message_thread_id=callback.message.message_thread_id,
                text="Укажите причину отказа:",
                reply_markup=make_reject_reason_keyboard(lead_id),
                ttl_sec=TTL_MENU_SEC,
            )
            return

        if action == "reject_reason":
            if len(parts) < 4:
                await sender.answer(callback)
                return
            lead_for_access = await repo.get_by_id(lead_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            reason_key = parts[3]
            data = await state.get_data()
            ref_data = data.get("message_ref")
            target_ref = MessageRef.from_dict(ref_data)
            if reason_key == "custom":
                await state.set_state(RejectState.waiting_for_custom_reason)
                await sender.answer(callback)
                await cleanup_inline_menu(callback, sender)
                await start_force_reply(callback, state, sender, "Введите свою причину отказа:")
                return

            reason = REJECT_REASON_LABELS.get(reason_key)
            if not reason:
                await sender.answer(callback)
                return

            lead = await service.reject_lead(
                lead_id,
                callback.from_user.id,
                reason=reason,
                source_ref=target_ref,
            )
            if lead:
                await session.commit()
                await state.clear()
                await sender.answer(callback, "Готово ✅")
                await cleanup_inline_menu(callback, sender)
                return
            await sender.answer(callback, "⚠️ Не удалось отклонить заявку.", show_alert=True)
            return

        if action == "clone":
            lead_for_access = await repo.get_by_id(lead_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            clone = await service.clone_lead(lead_id)
            if clone:
                await session.commit()
                await sender.answer(callback, "📋 Копия создана")
                return
            await sender.answer(callback, "⚠️ Не удалось создать копию.", show_alert=True)
            return

        if action == "note":
            lead_for_access = await repo.get_by_id(lead_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            await state.set_state(NoteState.waiting_for_text)
            await state.update_data(lead_id=lead_id, message_ref=source_ref.to_dict() if source_ref else None)
            await sender.answer(callback)
            await start_force_reply(callback, state, sender, "Введите заметку:")
            return

        if action == "remind":
            lead_for_access = await repo.get_by_id(lead_id)
            if not lead_for_access or not _manager_can_act(manager, lead_for_access):
                await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
                return
            await sender.answer(callback)
            menu_msg = await sender.send_ephemeral_text(
                chat_id=callback.message.chat.id,
                message_thread_id=callback.message.message_thread_id,
                text="Когда напомнить?",
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
            lead_for_access = await repo.get_by_id(lead_id)
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
                await start_force_reply(callback, state, sender, "Введите дату и время (ДД.ММ.ГГГГ ЧЧ:ММ):")
                return

            now = datetime.now(timezone.utc)
            if choice == "1h":
                remind_at = now + timedelta(hours=1)
            elif choice == "3h":
                remind_at = now + timedelta(hours=3)
            elif choice == "tomorrow":
                remind_at = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            else:
                await sender.answer(callback)
                return

            reminder = await ReminderService(repo, sender).schedule_reminder(
                lead_id=lead_id,
                manager_tg_id=callback.from_user.id,
                remind_at=remind_at,
                group_id=group_id,
            )
            await session.commit()
            if reminder:
                await sender.answer(callback, "Готово ✅")
                await cleanup_inline_menu(callback, sender)
            else:
                await sender.answer(callback, "⚠️ Не удалось поставить напоминание.", show_alert=True)
            return

    logger.warning(f"Unhandled lead action: {action} for lead_id={lead_id}")


@router.message(AmountState.waiting_for_amount)
async def handle_amount_input(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    group_id = _get_group_id(tenant)
    if not await reject_non_force_reply(message, state, sender):
        return

    amount = _parse_amount(message.text or "")
    if amount is None:
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
            text="Введите корректную сумму (число больше 0).",
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
        manager = await _get_manager(repo, message.from_user.id)
        if not manager:
            await cleanup_force_reply(sender, state, message)
            await state.clear()
            return
        lead_obj = await repo.get_by_id(int(lead_id))
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

        service = LeadService(repo, sender, group_id=group_id)
        lead = await service.mark_paid(int(lead_id), message.from_user.id, amount, target_ref)
        if lead:
            await session.commit()
        else:
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="⚠️ Не удалось перевести заявку в «Оплачено».",
                ttl_sec=TTL_ERROR_SEC,
            )

    await cleanup_force_reply(sender, state, message)
    await state.clear()


@router.message(RejectState.waiting_for_custom_reason)
async def handle_custom_reject(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    group_id = _get_group_id(tenant)
    if not await reject_non_force_reply(message, state, sender):
        return

    reason = (message.text or "").strip()
    if not reason:
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
            text="Введите причину отказа.",
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
        manager = await _get_manager(repo, message.from_user.id)
        if not manager:
            await cleanup_force_reply(sender, state, message)
            await state.clear()
            return
        lead_obj = await repo.get_by_id(int(lead_id))
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

        service = LeadService(repo, sender, group_id=group_id)
        lead = await service.reject_lead(int(lead_id), message.from_user.id, reason=reason, source_ref=target_ref)
        if lead:
            await session.commit()
        else:
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="⚠️ Не удалось отклонить заявку.",
                ttl_sec=TTL_ERROR_SEC,
            )

    await cleanup_force_reply(sender, state, message)
    await state.clear()


@router.message(NoteState.waiting_for_text)
async def handle_note_text(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    group_id = _get_group_id(tenant)

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
            text="Введите текст заметки.",
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
        manager = await _get_manager(repo, message.from_user.id)
        if not manager:
            await cleanup_force_reply(sender, state, message)
            await state.clear()
            return
        lead_obj = await repo.get_by_id(int(lead_id))
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

        service = LeadService(repo, sender, group_id=group_id)
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
    group_id = _get_group_id(tenant)

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
            text="Некорректный формат. Пример: 28.02.2026 14:30",
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
        manager = await _get_manager(repo, message.from_user.id)
        if not manager:
            await cleanup_force_reply(sender, state, message)
            await state.clear()
            return
        lead_obj = await repo.get_by_id(int(lead_id))
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


@router.message(F.reply_to_message)
async def handle_reply_note(message: Message, sender: TelegramSafeSender, tenant=None):
    group_id = _get_group_id(tenant)
    if not group_id or message.chat.id != group_id:
        return
    if not message.text or message.text.startswith("/"):
        return

    target_ref = MessageRef.from_reply(message.reply_to_message)
    if not target_ref:
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, message.from_user.id)
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

        record = await repo.get_card_message(target_ref.chat_id, target_ref.message_id)
        lead_id = record.lead_id if record else None
        if not lead_id:
            lead = await repo.get_lead_by_tg_message(target_ref.message_id, target_ref.topic_id)
            if lead:
                await repo.ensure_active_card_message(
                    lead_id=lead.id,
                    chat_id=target_ref.chat_id,
                    topic_id=target_ref.topic_id,
                    message_id=target_ref.message_id,
                )
                lead_id = lead.id

        if not lead_id:
            return

        lead_obj = await repo.get_by_id(lead_id)
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

        service = LeadService(repo, sender, group_id=group_id)
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


# ── Меню топика / Создание заявки ───────────────────────────────


@router.callback_query(F.data == "menu:create")
async def handle_create_lead(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender):
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, callback.from_user.id)
        if not manager:
            await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
            return

    await state.set_state(CreateLeadState.waiting_for_name)
    await sender.answer(callback)
    await cleanup_inline_menu(callback, sender)
    await start_force_reply(callback, state, sender, "Введите имя клиента:")


@router.message(CreateLeadState.waiting_for_name)
async def handle_create_lead_name(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):

    if not await reject_non_force_reply(message, state, sender):
        return
    name = (message.text or "").strip()
    if not name:
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
            text="Введите имя клиента.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return
    await state.update_data(name=name)
    await state.set_state(CreateLeadState.waiting_for_phone)
    await cleanup_force_reply(sender, state, message)
    await start_force_reply(message, state, sender, "Введите телефон клиента:")


@router.message(CreateLeadState.waiting_for_phone)
async def handle_create_lead_phone(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):

    if not await reject_non_force_reply(message, state, sender):
        return
    phone = (message.text or "").strip()
    
    if not phone:
        if not PHONE_RE.match(phone):
            return await sender.send_ephemeral_text('❌ Некорректный номер телефона. Попробуй ещё раз.')
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
            text="Введите телефон клиента.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return
    await state.update_data(phone=phone)
    await state.set_state(CreateLeadState.waiting_for_email)
    await cleanup_force_reply(sender, state, message)
    await start_force_reply(message, state, sender, "Введите почту клиента (или напишите skip):")


@router.callback_query(F.data == "create:skip:email")
async def handle_skip_email(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender):
    await state.update_data(email=None)
    await state.set_state(CreateLeadState.waiting_for_service)
    await sender.answer(callback)
    await cleanup_inline_menu(callback, sender)
    await delete_force_reply_prompt(sender, state)
    await start_force_reply(callback, state, sender, "Введите услугу (или напишите skip):")


@router.message(CreateLeadState.waiting_for_email)
async def handle_create_lead_email(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
 
    if not await reject_non_force_reply(message, state, sender):
        return
    email_raw = (message.text or "").strip()
    email_value = None
    if email_raw and email_raw.lower() not in {"skip", "-", "пропустить"}:
        if not EMAIL_RE.match(email_raw):
            return await sender.send_ephemeral_text('❌ Некорректный email. Попробуй ещё раз.')
        email_value = email_raw
    await state.update_data(email=email_value)
    await state.set_state(CreateLeadState.waiting_for_service)
    await cleanup_force_reply(sender, state, message)
    await start_force_reply(message, state, sender, "Введите услугу (или напишите skip):")


@router.callback_query(F.data == "create:skip:service")
async def handle_skip_service(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender):
    await state.update_data(service=None)
    await state.set_state(CreateLeadState.waiting_for_comment)
    await sender.answer(callback)
    await cleanup_inline_menu(callback, sender)
    await delete_force_reply_prompt(sender, state)
    await start_force_reply(callback, state, sender, "Введите комментарий:")


@router.message(CreateLeadState.waiting_for_service)
async def handle_create_lead_service(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
 
    if not await reject_non_force_reply(message, state, sender):
        return
    service_raw = (message.text or "").strip()
    service_value = None
    if service_raw and service_raw.lower() not in {"skip", "-", "пропустить"}:
        service_value = service_raw
    await state.update_data(service=service_value)
    await state.set_state(CreateLeadState.waiting_for_comment)
    await cleanup_force_reply(sender, state, message)
    await start_force_reply(message, state, sender, "Введите комментарий:")


@router.message(CreateLeadState.waiting_for_comment)
async def handle_create_lead_comment(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    group_id = _get_group_id(tenant)

    if not await reject_non_force_reply(message, state, sender):
        return
    comment = (message.text or "").strip()
    if not comment:
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
            text="Введите комментарий.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    data = await state.get_data()
    payload = {
        "name": data.get("name"),
        "phone": data.get("phone"),
        "email": data.get("email"),
        "service": data.get("service"),
        "comment": comment,
        "source": "manual",
        "status": LeadStatus.NEW,
    }

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, message.from_user.id)
        if not manager:
            await cleanup_force_reply(sender, state, message)
            await state.clear()
            return

        service = LeadService(repo, sender, group_id=group_id)
        lead = await service.create_lead(payload)
        await session.commit()

    await cleanup_force_reply(sender, state, message)
    await sender.send_ephemeral_text(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=f"Заявка #{lead.id} создана.",
        ttl_sec=TTL_MENU_SEC,
    )
    await state.clear()


@router.callback_query(F.data.startswith("menu:period:"))
async def handle_menu_period(callback: CallbackQuery, sender: TelegramSafeSender):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await sender.answer(callback)
        return

    status_raw = parts[2]
    if status_raw not in {"new", "in_progress", "paid", "success", "rejected"}:
        await sender.answer(callback)
        return

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, callback.from_user.id)
        if not manager:
            await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
            return

        if len(parts) == 3:
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="Сегодня", callback_data=f"menu:period:{status_raw}:today"),
                InlineKeyboardButton(text="Неделя", callback_data=f"menu:period:{status_raw}:week"),
                InlineKeyboardButton(text="Месяц", callback_data=f"menu:period:{status_raw}:month"),
            )
            await sender.answer(callback)
            await sender.send_ephemeral_text(
                chat_id=callback.message.chat.id,
                message_thread_id=callback.message.message_thread_id,
                text="Выберите период:",
                reply_markup=builder.as_markup(),
                ttl_sec=TTL_MENU_SEC,
            )
            return

        period = parts[3]
        now = datetime.now(timezone.utc)
        date_from = {
            "today": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "week": now - timedelta(days=7),
            "month": now - timedelta(days=30),
        }.get(period)
        if not date_from:
            await sender.answer(callback)
            return

        status = LeadStatus(status_raw)
        count = await repo.count_by_status_period(status, date_from=date_from, date_to=now)

    period_label = {"today": "сегодня", "week": "за неделю", "month": "за месяц"}[period]
    await sender.answer(callback)
    await cleanup_inline_menu(callback, sender)
    await sender.send_ephemeral_text(
        chat_id=callback.message.chat.id,
        message_thread_id=callback.message.message_thread_id,
        text=f"Заявок {period_label}: {count}",
        ttl_sec=TTL_MENU_SEC,
    )
