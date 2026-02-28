from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.states.input import InputFlow
from app.bot.utils.force_reply import reject_non_force_reply, cleanup_force_reply
from app.telegram.safe_sender import TelegramSafeSender

router = Router()


@router.message(InputFlow.waiting_text)
async def handle_input_flow(message: Message, state: FSMContext, sender: TelegramSafeSender):
    if not await reject_non_force_reply(message, state, sender):
        return
    await cleanup_force_reply(sender, state, message)
    await state.clear()
