from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from app.bot.constants.ttl import TTL_ERROR_SEC, TTL_MENU_SEC
from app.bot.utils.force_reply import (
    cleanup_force_reply,
    delete_force_reply_prompt,
    reject_non_force_reply,
    start_force_reply,
)
from app.bot.utils.menu_cleanup import cleanup_inline_menu, cleanup_inline_menu_by_id
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

_SKIP_WORDS = {"skip", "-", "пропустить"}
_CANCEL_WORDS = {"/cancel", "cancel", "отмена"}


def _is_cancel(text: str | None) -> bool:
    if not text:
        return False
    return text.strip().lower() in _CANCEL_WORDS


def _build_skip_keyboard(kind: str):
    builder = InlineKeyboardBuilder()
    if kind == "email":
        builder.row(InlineKeyboardButton(text="Пропустить", callback_data="create:skip:email"))
    elif kind == "service":
        builder.row(InlineKeyboardButton(text="Пропустить", callback_data="create:skip:service"))
    return builder.as_markup()


def _build_confirm_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Создать", callback_data="create:confirm:yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="create:confirm:cancel"),
    )
    return builder.as_markup()


def _render_confirmation_text(data: dict) -> str:
    email = data.get("email") or "—"
    service = data.get("service") or "—"
    return (
        "🆕 <b>Проверьте заявку перед созданием</b>\n\n"
        f"👤 Имя: {data.get('name')}\n"
        f"📱 Телефон: {data.get('phone')}\n"
        f"📧 Email: {email}\n"
        f"🛠 Услуга: {service}\n"
        f"💬 Комментарий: {data.get('comment')}\n\n"
        "Создать заявку?"
    )


async def _store_skip_menu(state: FSMContext, menu_message: Message) -> None:
    await state.update_data(
        create_skip_menu_chat_id=menu_message.chat.id,
        create_skip_menu_message_id=menu_message.message_id,
        create_skip_menu_thread_id=menu_message.message_thread_id,
    )


async def _show_skip_menu(
    sender: TelegramSafeSender,
    state: FSMContext,
    *,
    chat_id: int,
    message_thread_id: int | None,
    kind: str,
) -> None:
    menu_message = await sender.send_ephemeral_text(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        text="Можно пропустить этот шаг.",
        reply_markup=_build_skip_keyboard(kind),
        ttl_sec=300,
    )
    await _store_skip_menu(state, menu_message)


async def _cleanup_skip_menu(sender: TelegramSafeSender, state: FSMContext) -> None:
    data = await state.get_data()
    await cleanup_inline_menu_by_id(
        sender,
        chat_id=data.get("create_skip_menu_chat_id"),
        message_id=data.get("create_skip_menu_message_id"),
        thread_id=data.get("create_skip_menu_thread_id"),
        done_text="",
    )
    await state.update_data(
        create_skip_menu_chat_id=None,
        create_skip_menu_message_id=None,
        create_skip_menu_thread_id=None,
    )


async def _cancel_create_flow(
    sender: TelegramSafeSender,
    state: FSMContext,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> None:
    if message is not None:
        await cleanup_force_reply(sender, state, message)
    else:
        await delete_force_reply_prompt(sender, state)

    if callback is not None:
        await cleanup_inline_menu(callback, sender, done_text="Создание заявки отменено.")

    await _cleanup_skip_menu(sender, state)
    await state.clear()


@router.callback_query(F.data == "menu:create")
async def handle_create_lead(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender, tenant=None):
    tenant_id = tenant.id if tenant else None
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, callback.from_user.id, tenant_id=tenant_id)
        if not manager:
            await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
            return

    await state.clear()
    await state.set_state(CreateLeadState.waiting_for_name)
    await state.update_data(tenant_id=tenant_id)
    await sender.answer(callback)
    await start_force_reply(callback, state, sender, "ℹ️ Введите имя клиента:")


@router.callback_query(F.data == "create:skip:email")
async def handle_skip_email(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender):
    if await state.get_state() != CreateLeadState.waiting_for_email.state:
        await sender.answer(callback)
        return
    await state.update_data(email=None)
    await state.set_state(CreateLeadState.waiting_for_service)
    await sender.answer(callback)
    await cleanup_inline_menu(callback, sender, done_text="Шаг пропущен.")
    await delete_force_reply_prompt(sender, state)
    await _cleanup_skip_menu(sender, state)
    await start_force_reply(callback, state, sender, "ℹ️ Введите услугу (или напишите «пропустить»):")
    await _show_skip_menu(
        sender,
        state,
        chat_id=callback.message.chat.id,
        message_thread_id=callback.message.message_thread_id,
        kind="service",
    )


@router.callback_query(F.data == "create:skip:service")
async def handle_skip_service(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender):
    if await state.get_state() != CreateLeadState.waiting_for_service.state:
        await sender.answer(callback)
        return
    await state.update_data(service=None)
    await state.set_state(CreateLeadState.waiting_for_comment)
    await sender.answer(callback)
    await cleanup_inline_menu(callback, sender, done_text="Шаг пропущен.")
    await delete_force_reply_prompt(sender, state)
    await _cleanup_skip_menu(sender, state)
    await start_force_reply(callback, state, sender, "ℹ️ Введите комментарий:")


@router.callback_query(F.data.startswith("create:confirm:"))
async def handle_create_confirm(callback: CallbackQuery, state: FSMContext, sender: TelegramSafeSender, tenant=None):
    decision = callback.data.split(":")[2]
    if decision == "cancel":
        await sender.answer(callback, "Отменено.")
        await _cancel_create_flow(sender, state, callback=callback)
        return

    data = await state.get_data()
    name = data.get("name")
    phone = data.get("phone")
    comment = data.get("comment")
    if not name or not phone or not comment:
        await sender.answer(callback, "⚠️ Данные заявки устарели. Начните заново.", show_alert=True)
        await _cancel_create_flow(sender, state, callback=callback)
        return

    tenant_id = tenant.id if tenant else data.get("tenant_id")
    group_id = _get_group_id(tenant) or (callback.message.chat.id if callback.message and callback.message.chat.id < 0 else None)
    if not group_id:
        await sender.answer(callback, "⚠️ Группа не настроена. Выполните /setup.", show_alert=True)
        return

    payload = {
        "name": name,
        "phone": phone,
        "email": data.get("email"),
        "service": data.get("service"),
        "comment": comment,
        "source": "manual",
        "status": LeadStatus.NEW,
        "tenant_id": tenant_id,
    }

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, callback.from_user.id, tenant_id=tenant_id)
        if not manager:
            await sender.answer(callback, NO_ACCESS_TEXT, show_alert=True)
            await _cancel_create_flow(sender, state, callback=callback)
            return

        service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
        lead = await service.create_lead(payload)
        await session.commit()

    async with AsyncSessionLocal() as post_session:
        post_repo = LeadRepository(post_session)
        post_service = LeadService(post_repo, sender, group_id=group_id, tenant_id=tenant_id)
        try:
            await post_service.sync_new_lead_card(lead.id)
            await post_session.commit()
        except Exception:
            await post_session.rollback()
            logger.exception(
                "lead_create_post_commit_sync_failed lead_id={} tenant_id={} group_id={} source=manual",
                lead.id,
                tenant_id,
                group_id,
            )

    await sender.answer(callback, "✅ Заявка создана.")
    await cleanup_inline_menu(callback, sender, done_text=f"✅ Заявка #{lead.id} создана.")
    await _cancel_create_flow(sender, state)


@router.message(CreateLeadState.waiting_for_name)
async def handle_create_lead_name(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    if not await reject_non_force_reply(message, state, sender):
        return
    if _is_cancel(message.text):
        await _cancel_create_flow(sender, state, message=message)
        return

    name = (message.text or "").strip()
    if len(name) < 2:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Имя должно быть не короче 2 символов.",
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
    if _is_cancel(message.text):
        await _cancel_create_flow(sender, state, message=message)
        return

    phone = (message.text or "").strip()
    if not phone:
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
    await _show_skip_menu(
        sender,
        state,
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        kind="email",
    )


@router.message(CreateLeadState.waiting_for_email)
async def handle_create_lead_email(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    if not await reject_non_force_reply(message, state, sender):
        return
    if _is_cancel(message.text):
        await _cancel_create_flow(sender, state, message=message)
        return

    email_raw = (message.text or "").strip()
    email_value = None
    if email_raw and email_raw.lower() not in _SKIP_WORDS:
        if not EMAIL_RE.match(email_raw):
            await sender.send_ephemeral_text(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="⛔️ Некорректный email. Попробуйте ещё раз или нажмите «Пропустить».",
                ttl_sec=TTL_ERROR_SEC,
            )
            return
        email_value = email_raw

    await state.update_data(email=email_value)
    await state.set_state(CreateLeadState.waiting_for_service)
    await cleanup_force_reply(sender, state, message)
    await _cleanup_skip_menu(sender, state)
    await start_force_reply(message, state, sender, "ℹ️ Введите услугу (или напишите «пропустить»):")
    await _show_skip_menu(
        sender,
        state,
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        kind="service",
    )


@router.message(CreateLeadState.waiting_for_service)
async def handle_create_lead_service(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    if not await reject_non_force_reply(message, state, sender):
        return
    if _is_cancel(message.text):
        await _cancel_create_flow(sender, state, message=message)
        return

    service_raw = (message.text or "").strip()
    service_value = None if not service_raw or service_raw.lower() in _SKIP_WORDS else service_raw

    await state.update_data(service=service_value)
    await state.set_state(CreateLeadState.waiting_for_comment)
    await cleanup_force_reply(sender, state, message)
    await _cleanup_skip_menu(sender, state)
    await start_force_reply(message, state, sender, "ℹ️ Введите комментарий:")


@router.message(CreateLeadState.waiting_for_comment)
async def handle_create_lead_comment(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
    tenant=None,
):
    if not await reject_non_force_reply(message, state, sender):
        return
    if _is_cancel(message.text):
        await _cancel_create_flow(sender, state, message=message)
        return

    comment = (message.text or "").strip()
    if len(comment) < 2:
        await sender.send_ephemeral_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text="⛔️ Комментарий должен быть не короче 2 символов.",
            ttl_sec=TTL_ERROR_SEC,
        )
        return

    await state.update_data(comment=comment)
    await state.set_state(CreateLeadState.waiting_for_confirm)
    await cleanup_force_reply(sender, state, message)

    data = await state.get_data()
    await sender.send_message(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=_render_confirmation_text(data),
        reply_markup=_build_confirm_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("menu:period:"))
async def handle_menu_period(callback: CallbackQuery, sender: TelegramSafeSender, tenant=None):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await sender.answer(callback)
        return

    status_raw = parts[2]
    if status_raw not in {"new", "in_progress", "paid", "success", "rejected"}:
        await sender.answer(callback)
        return

    tenant_id = tenant.id if tenant else None
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        manager = await _get_manager(repo, callback.from_user.id, tenant_id=tenant_id)
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
        count = await repo.count_by_status_period(
            status,
            date_from=date_from,
            date_to=now,
            tenant_id=tenant_id,
        )

    period_label = {"today": "сегодня", "week": "за неделю", "month": "за месяц"}[period]
    await sender.answer(callback)
    await cleanup_inline_menu(callback, sender)
    await sender.send_ephemeral_text(
        chat_id=callback.message.chat.id,
        message_thread_id=callback.message.message_thread_id,
        text=f"ℹ️ Заявок {period_label}: {count}.",
        ttl_sec=TTL_MENU_SEC,
    )


@router.message(CreateLeadState.waiting_for_confirm)
async def handle_create_waiting_confirm(
    message: Message,
    state: FSMContext,
    sender: TelegramSafeSender,
):
    if _is_cancel(message.text):
        await _cancel_create_flow(sender, state, message=message)
        return

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
        text="ℹ️ Используйте кнопки подтверждения: «Создать» или «Отмена».",
        ttl_sec=TTL_ERROR_SEC,
    )
