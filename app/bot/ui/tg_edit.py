from __future__ import annotations

from loguru import logger
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from app.bot.ui.message_ref import MessageRef
from app.telegram.safe_sender import TelegramSafeSender


_NOT_EDITABLE_TOKENS = (
    "message to edit not found",
    "message_id_invalid",
    "message can't be edited",
)


async def edit_keyboard(
    sender: TelegramSafeSender,
    ref: MessageRef,
    keyboard: InlineKeyboardMarkup | None,
) -> bool:
    try:
        await sender.edit_message_reply_markup(
            chat_id=ref.chat_id,
            message_id=ref.message_id,
            reply_markup=keyboard,
            thread_id=ref.topic_id,
        )
        return True
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if any(token in msg for token in _NOT_EDITABLE_TOKENS):
            logger.warning(f"edit_keyboard failed (not editable): {ref} err={e}")
            return False
        logger.warning(f"edit_keyboard failed: {ref} err={e}")
        return False
    except Exception as e:
        logger.error(f"edit_keyboard unexpected error: {ref} err={e}")
        return False


async def edit_text(
    sender: TelegramSafeSender,
    ref: MessageRef,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> bool:
    try:
        await sender.edit_message_text(
            chat_id=ref.chat_id,
            message_id=ref.message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
            thread_id=ref.topic_id,
        )
        return True
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            if keyboard is not None:
                return await edit_keyboard(sender, ref, keyboard)
            return True
        if any(token in msg for token in _NOT_EDITABLE_TOKENS):
            logger.warning(f"edit_text failed (not editable): {ref} err={e}")
            return False
        logger.warning(f"edit_text failed: {ref} err={e}")
        return False
    except Exception as e:
        logger.error(f"edit_text unexpected error: {ref} err={e}")
        return False


async def archive_message(
    sender: TelegramSafeSender,
    ref: MessageRef,
    new_text: str,
) -> bool:
    try:
        await sender.edit_message_text(
            chat_id=ref.chat_id,
            message_id=ref.message_id,
            text=new_text,
            reply_markup=None,
            parse_mode="HTML",
            thread_id=ref.topic_id,
        )
        return True
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            return True
        if any(token in msg for token in _NOT_EDITABLE_TOKENS):
            logger.warning(f"archive_message failed (not editable): {ref} err={e}")
            return False
        logger.warning(f"archive_message failed: {ref} err={e}")
        return False
    except Exception as e:
        logger.error(f"archive_message unexpected error: {ref} err={e}")
        return False
