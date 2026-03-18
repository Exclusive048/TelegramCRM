from aiogram.filters import Command
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.utils.force_reply import delete_force_reply_prompt
from app.telegram.safe_sender import TelegramSafeSender

router = Router(name="crm.cancel")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, sender: TelegramSafeSender) -> None:
    await delete_force_reply_prompt(sender, state)
    await state.clear()
    await sender.send_ephemeral_text(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text="✅ Действие отменено.",
        ttl_sec=5,
    )
