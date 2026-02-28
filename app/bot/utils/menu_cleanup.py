from __future__ import annotations

from aiogram.types import CallbackQuery
from loguru import logger

from app.bot.constants.ttl import TTL_MENU_SEC
from app.telegram.safe_sender import TelegramSafeSender


async def _cleanup_message(
    sender: TelegramSafeSender,
    *,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    done_text: str,
) -> None:
    try:
        await sender.delete_message(chat_id=chat_id, message_id=message_id, thread_id=thread_id)
        logger.info(
            "menu_cleanup delete ok chat_id={} message_id={}",
            chat_id,
            message_id,
        )
        return
    except Exception as delete_err:
        logger.warning(
            "menu_cleanup delete failed chat_id={} message_id={} err={}",
            chat_id,
            message_id,
            delete_err,
        )

    try:
        await sender.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
            thread_id=thread_id,
        )
        logger.info(
            "menu_cleanup markup cleared chat_id={} message_id={}",
            chat_id,
            message_id,
        )
    except Exception as markup_err:
        logger.warning(
            "menu_cleanup markup clear failed chat_id={} message_id={} err={}",
            chat_id,
            message_id,
            markup_err,
        )

    try:
        await sender.edit_text(
            chat_id=chat_id,
            message_id=message_id,
            text=done_text,
            thread_id=thread_id,
        )
        logger.info(
            "menu_cleanup text set chat_id={} message_id={}",
            chat_id,
            message_id,
        )
    except Exception as text_err:
        logger.warning(
            "menu_cleanup text set failed chat_id={} message_id={} err={}",
            chat_id,
            message_id,
            text_err,
        )

    try:
        await sender.schedule_delete(
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            ttl_sec=TTL_MENU_SEC,
        )
    except Exception as schedule_err:
        logger.debug(
            "menu_cleanup schedule delete failed chat_id={} message_id={} err={}",
            chat_id,
            message_id,
            schedule_err,
        )


async def cleanup_inline_menu(
    callback: CallbackQuery,
    sender: TelegramSafeSender,
    *,
    done_text: str = "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e \u2705",
) -> None:
    if callback.message is None:
        return
    await _cleanup_message(
        sender,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        thread_id=callback.message.message_thread_id,
        done_text=done_text,
    )


async def cleanup_inline_menu_by_id(
    sender: TelegramSafeSender,
    *,
    chat_id: int | None,
    message_id: int | None,
    thread_id: int | None = None,
    done_text: str = "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e \u2705",
) -> None:
    if chat_id is None or message_id is None:
        return
    await _cleanup_message(
        sender,
        chat_id=chat_id,
        message_id=message_id,
        thread_id=thread_id,
        done_text=done_text,
    )
