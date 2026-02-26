from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from loguru import logger

from app.db.database import AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.services.lead_service import LeadService
from app.core.permissions import is_any_manager

router = Router()


async def _check_manager(callback: CallbackQuery) -> bool:
    """Проверяет что нажавший является менеджером"""
    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        ok = await is_any_manager(repo, callback.from_user.id)
    if not ok:
        await callback.answer("⛔️ У вас нет доступа к этому действию.", show_alert=True)
    return ok


# ── Взять в обработку ────────────────────────────────

@router.callback_query(F.data.startswith("lead:take:"))
async def handle_take(callback: CallbackQuery):
    if not await _check_manager(callback):
        return
    lead_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        service = LeadService(LeadRepository(session), callback.bot)
        lead = await service.take_in_progress(lead_id, callback.from_user.id)
    if lead:
        await callback.answer("✅ Заявка взята в обработку")
    else:
        await callback.answer("⚠️ Не удалось взять заявку.", show_alert=True)


# ── Закрыть сделку ────────────────────────────────────

@router.callback_query(F.data.startswith("lead:close:"))
async def handle_close(callback: CallbackQuery):
    if not await _check_manager(callback):
        return
    lead_id = int(callback.data.split(":")[2])
    async with AsyncSessionLocal() as session:
        service = LeadService(LeadRepository(session), callback.bot)
        lead = await service.close_lead(lead_id, callback.from_user.id)
    if lead:
        await callback.answer("🎉 Сделка закрыта!")
    else:
        await callback.answer("⚠️ Ошибка.", show_alert=True)


# ── Отклонить ─────────────────────────────────────────

class RejectState(StatesGroup):
    waiting_for_reason = State()


@router.callback_query(F.data.startswith("lead:reject:"))
async def handle_reject_start(callback: CallbackQuery, state: FSMContext):
    if not await _check_manager(callback):
        return
    lead_id = int(callback.data.split(":")[2])
    await state.set_state(RejectState.waiting_for_reason)
    await state.update_data(lead_id=lead_id)
    await callback.answer()

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Нет бюджета",     callback_data="reject_reason:Нет бюджета"),
        InlineKeyboardButton(text="Не дозвонились",  callback_data="reject_reason:Не дозвонились"),
    )
    builder.row(
        InlineKeyboardButton(text="Не целевой",      callback_data="reject_reason:Не целевой"),
        InlineKeyboardButton(text="Передумал",       callback_data="reject_reason:Передумал"),
    )
    builder.row(InlineKeyboardButton(text="✏️ Своя причина", callback_data="reject_reason:__custom__"))

    await callback.message.reply(
        "❌ Укажите причину отказа:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("reject_reason:"))
async def handle_reject_reason_btn(callback: CallbackQuery, state: FSMContext):
    reason = callback.data.split(":", 1)[1]
    data = await state.get_data()
    lead_id = data.get("lead_id")

    if reason == "__custom__":
        await state.set_state(RejectState.waiting_for_reason)
        await callback.message.edit_text("✏️ Напишите причину отказа:")
        return

    await state.clear()
    await callback.message.edit_text(f"⏳ Отклоняю...")
    async with AsyncSessionLocal() as session:
        service = LeadService(LeadRepository(session), callback.bot)
        lead = await service.reject_lead(lead_id, callback.from_user.id, reason)
    if lead:
        await callback.message.edit_text(f"❌ Заявка #{lead_id} отклонена.\nПричина: {reason}")
    else:
        await callback.message.edit_text("⚠️ Ошибка при отклонении.")


@router.message(RejectState.waiting_for_reason)
async def handle_reject_custom_reason(message, state: FSMContext):
    data = await state.get_data()
    lead_id = data["lead_id"]
    reason = message.text.strip()
    await state.clear()
    async with AsyncSessionLocal() as session:
        service = LeadService(LeadRepository(session), message.bot)
        lead = await service.reject_lead(lead_id, message.from_user.id, reason)
    if lead:
        await message.answer(f"❌ Заявка #{lead_id} отклонена.")
    else:
        await message.answer("⚠️ Ошибка.")


# ── Добавить заметку ──────────────────────────────────

class CommentState(StatesGroup):
    waiting_for_comment = State()


@router.callback_query(F.data.startswith("lead:comment:"))
async def handle_comment_start(callback: CallbackQuery, state: FSMContext):
    if not await _check_manager(callback):
        return
    lead_id = int(callback.data.split(":")[2])
    await state.set_state(CommentState.waiting_for_comment)
    await state.update_data(lead_id=lead_id)
    await callback.answer()
    await callback.message.reply("📝 Введите заметку к заявке:")


@router.message(CommentState.waiting_for_comment)
async def handle_comment_text(message, state: FSMContext):
    data = await state.get_data()
    lead_id = data["lead_id"]
    await state.clear()

    async with AsyncSessionLocal() as session:
        repo = LeadRepository(session)
        await repo.add_comment(lead_id=lead_id, text=message.text, author=message.from_user.full_name)
        # Фикс: перерисовываем карточку чтобы заметка отобразилась
        service = LeadService(repo, message.bot)
        await service.refresh_card(lead_id)

    await message.answer(f"✅ Заметка добавлена к заявке #{lead_id}.")
