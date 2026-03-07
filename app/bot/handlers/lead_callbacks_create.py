from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.constants.ttl import TTL_ERROR_SEC, TTL_MENU_SEC
from app.bot.utils.force_reply import (
    cleanup_force_reply,
    delete_force_reply_prompt,
    reject_non_force_reply,
    start_force_reply,
)
from app.bot.utils.menu_cleanup import cleanup_inline_menu
from app.db.database import AsyncSessionLocal
from app.db.models.lead import LeadStatus
from app.db.repositories.lead_repository import LeadRepository
from app.services.lead_service import LeadService
from app.telegram.safe_sender import TelegramSafeSender

from .lead_callbacks_shared import (
    CreateLeadState,
    EMAIL_RE,
    NO_ACCESS_TEXT,
    PHONE_RE,
    _get_group_id,
    _get_manager,
)

router = Router()


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
    await start_force_reply(callback, state, sender, "ℹ️ Введите имя клиента:")


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
            text="⛔️ Введите имя клиента.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return
    await state.update_data(name=name)
    await state.set_state(CreateLeadState.waiting_for_phone)
    await cleanup_force_reply(sender, state, message)
    await start_force_reply(message, state, sender, "ℹ️ Введите телефон клиента:")


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
            text="⛔️ Введите телефон клиента.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return
    if not PHONE_RE.match(phone):
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Некорректный номер телефона. Попробуйте ещё раз.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return
    await state.update_data(phone=phone)
    await state.set_state(CreateLeadState.waiting_for_email)
    await cleanup_force_reply(sender, state, message)
    await start_force_reply(message, state, sender, "ℹ️ Введите почту клиента (или напишите «пропустить»):")


@router.callback_query(F.data == "create:skip:email")
async def handle_skip_email(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender):
    await state.update_data(email=None)
    await state.set_state(CreateLeadState.waiting_for_service)
    await sender.answer(callback)
    await cleanup_inline_menu(callback, sender)
    await delete_force_reply_prompt(sender, state)
    await start_force_reply(callback, state, sender, "ℹ️ Введите услугу (или напишите «пропустить»):")


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
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="⛔️ Некорректный адрес электронной почты. Попробуйте ещё раз.",
                ttl_sec=TTL_ERROR_SEC,
            )
            return
        email_value = email_raw
    await state.update_data(email=email_value)
    await state.set_state(CreateLeadState.waiting_for_service)
    await cleanup_force_reply(sender, state, message)
    await start_force_reply(message, state, sender, "ℹ️ Введите услугу (или напишите «пропустить»):")


@router.callback_query(F.data == "create:skip:service")
async def handle_skip_service(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender):
    await state.update_data(service=None)
    await state.set_state(CreateLeadState.waiting_for_comment)
    await sender.answer(callback)
    await cleanup_inline_menu(callback, sender)
    await delete_force_reply_prompt(sender, state)
    await start_force_reply(callback, state, sender, "ℹ️ Введите комментарий:")


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
    await start_force_reply(message, state, sender, "ℹ️ Введите комментарий:")


@router.message(CreateLeadState.waiting_for_comment)
async def handle_create_lead_comment(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    group_id = _get_group_id(tenant) or (message.chat.id if message.chat.id < 0 else None)

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
            text="⛔️ Введите комментарий.",
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
        text=f"✅ Заявка #{lead.id} создана.",
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
                text="ℹ️ Выберите период.",
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
        text=f"ℹ️ Заявок {period_label}: {count}.",
        ttl_sec=TTL_MENU_SEC,
    )


