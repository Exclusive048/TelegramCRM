from __future__ import annotations

from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ForceReply
from loguru import logger

from app.bot.constants.ttl import TTL_INPUT_SEC
from app.telegram.safe_sender import TelegramSafeSender

_PROMPT_ID_KEY = "force_reply_prompt_id"
_PROMPT_CHAT_KEY = "force_reply_chat_id"
_PROMPT_THREAD_KEY = "force_reply_thread_id"
_PROMPT_USER_KEY = "force_reply_user_id"


async def start_force_reply(
    event: Message | CallbackQuery,
    state: FSMContext,
    sender: TelegramSafeSender,
    text: str,
    *,
    ttl_sec: int = TTL_INPUT_SEC,
    **kwargs: Any,
) -> Message | None:
    message = event.message if isinstance(event, CallbackQuery) else event
    if message is None:
        return None

    data = await state.get_data()
    prompt_id = data.get(_PROMPT_ID_KEY)
    if prompt_id:
        try:
            await sender.delete_message(
                chat_id=message.chat.id,
                message_id=prompt_id,
                thread_id=message.message_thread_id,
            )
        except Exception as e:
            logger.debug("force_reply cleanup previous prompt failed err={}", e)

    prompt = await sender.send_text(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        text=text,
        reply_markup=ForceReply(selective=True),
        **kwargs,
    )
    await sender.schedule_delete(
        chat_id=prompt.chat.id,
        message_id=prompt.message_id,
        thread_id=prompt.message_thread_id,
        ttl_sec=ttl_sec,
    )
    await state.update_data(
        **{
            _PROMPT_ID_KEY: prompt.message_id,
            _PROMPT_CHAT_KEY: prompt.chat.id,
            _PROMPT_THREAD_KEY: prompt.message_thread_id,
            _PROMPT_USER_KEY: event.from_user.id if event.from_user else None,
        }
    )
    return prompt


async def is_force_reply(message: Message, state: FSMContext) -> bool:
    data = await state.get_data()
    prompt_id = data.get(_PROMPT_ID_KEY)
    chat_id = data.get(_PROMPT_CHAT_KEY)
    thread_id = data.get(_PROMPT_THREAD_KEY)
    user_id = data.get(_PROMPT_USER_KEY)

    logger.debug(f"is_force_reply check: prompt_id={prompt_id} chat_id={chat_id} thread_id={thread_id} user_id={user_id}")
    logger.debug(f"is_force_reply message: chat={message.chat.id} thread={message.message_thread_id} user={message.from_user.id if message.from_user else None} reply_to={message.reply_to_message.message_id if message.reply_to_message else None}")

    if not prompt_id or not user_id:
        logger.debug("is_force_reply FAIL: no prompt_id or user_id")
        return False
    if message.chat.id != chat_id:
        logger.debug(f"is_force_reply FAIL: chat mismatch {message.chat.id} != {chat_id}")
        return False
    if message.message_thread_id != thread_id:
        logger.debug(f"is_force_reply FAIL: thread mismatch {message.message_thread_id} != {thread_id}")
        return False
    if message.from_user is None or message.from_user.id != user_id:
        logger.debug("is_force_reply FAIL: user mismatch")
        return False
    if message.reply_to_message is None:
        logger.debug("is_force_reply FAIL: no reply_to_message")
        return False
    result = message.reply_to_message.message_id == prompt_id
    logger.debug(f"is_force_reply result={result} reply_to={message.reply_to_message.message_id} prompt={prompt_id}")
    return True


async def reject_non_force_reply(message: Message, state: FSMContext, sender: TelegramSafeSender) -> bool:
    if await is_force_reply(message, state):
        return True
    try:
        await sender.delete_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            thread_id=message.message_thread_id,
        )
    except Exception as e:
        logger.debug("force_reply delete garbage failed err={}", e)
    return False


async def cleanup_force_reply(sender: TelegramSafeSender, state: FSMContext, message: Message | None = None) -> None:
    data = await state.get_data()
    prompt_id = data.get(_PROMPT_ID_KEY)
    prompt_chat_id = data.get(_PROMPT_CHAT_KEY)
    prompt_thread_id = data.get(_PROMPT_THREAD_KEY)

    if message is not None:
        try:
            await sender.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id,
                thread_id=message.message_thread_id,
            )
        except Exception as e:
            logger.debug("force_reply delete reply failed err={}", e)

    if prompt_id and prompt_chat_id is not None:
        try:
            await sender.delete_message(
                chat_id=prompt_chat_id,
                message_id=prompt_id,
                thread_id=prompt_thread_id,
            )
        except Exception as e:
            logger.debug("force_reply delete prompt failed err={}", e)

    await state.update_data(
        **{
            _PROMPT_ID_KEY: None,
            _PROMPT_CHAT_KEY: None,
            _PROMPT_THREAD_KEY: None,
            _PROMPT_USER_KEY: None,
        }
    )


async def delete_force_reply_prompt(sender: TelegramSafeSender, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_id = data.get(_PROMPT_ID_KEY)
    prompt_chat_id = data.get(_PROMPT_CHAT_KEY)
    prompt_thread_id = data.get(_PROMPT_THREAD_KEY)
    if not prompt_id or prompt_chat_id is None:
        return
    try:
        await sender.delete_message(
            chat_id=prompt_chat_id,
            message_id=prompt_id,
            thread_id=prompt_thread_id,
        )
    except Exception as e:
        logger.debug("force_reply delete prompt failed err={}", e)
    await state.update_data(
        **{
            _PROMPT_ID_KEY: None,
            _PROMPT_CHAT_KEY: None,
            _PROMPT_THREAD_KEY: None,
            _PROMPT_USER_KEY: None,
        }
    )
