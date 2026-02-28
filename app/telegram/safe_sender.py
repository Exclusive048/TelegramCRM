from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from loguru import logger

from app.telegram.html_utils import html_escape

if TYPE_CHECKING:
    from app.services.message_deletion_service import MessageDeletionService

@dataclass(frozen=True, slots=True)
class RateKey:
    chat_id: int
    thread_id: int | None


class ChatRateLimiter:
    def __init__(
        self,
        min_delay_sec: float = 1.05,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.min_delay_sec = min_delay_sec
        self._clock = clock
        self._last_sent: dict[RateKey, float] = {}
        self._locks: dict[RateKey, asyncio.Lock] = {}

    async def wait(self, key: RateKey) -> None:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        async with lock:
            now = self._clock()
            last = self._last_sent.get(key, 0.0)
            delay = self.min_delay_sec - (now - last)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_sent[key] = self._clock()


class TelegramSafeSender:
    def __init__(
        self,
        bot: Bot,
        limiter: ChatRateLimiter,
        *,
        max_attempts: int = 6,
        jitter: float = 0.25,
        deletion_service: "MessageDeletionService | None" = None,
    ):
        self.bot = bot
        self.limiter = limiter
        self.max_attempts = max_attempts
        self.jitter = jitter
        self.deletion_service = deletion_service

    async def _call(
        self,
        method: str,
        key: RateKey,
        func: Callable[..., Awaitable[Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        attempt = 0
        while True:
            attempt += 1
            await self.limiter.wait(key)
            try:
                return await func(*args, **kwargs)
            except TelegramRetryAfter as e:
                retry_after = float(getattr(e, "retry_after", 0))
                sleep_for = retry_after + 1.0 + random.random() * self.jitter
                logger.warning(
                    "telegram_flood_hit chat_id={} thread_id={} method={} retry_after={} attempt={}",
                    key.chat_id,
                    key.thread_id,
                    method,
                    retry_after,
                    attempt,
                )
                if attempt >= self.max_attempts:
                    raise
                await asyncio.sleep(sleep_for)
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "can't parse entities" in msg or "unsupported start tag" in msg:
                    raw_text = None
                    if isinstance(kwargs.get("text"), str):
                        raw_text = kwargs.get("text")
                    elif isinstance(kwargs.get("caption"), str):
                        raw_text = kwargs.get("caption")
                    snippet = ""
                    if raw_text:
                        snippet = raw_text.replace("\n", " ")
                        if len(snippet) > 80:
                            snippet = snippet[:80] + "..."
                    logger.warning(
                        "telegram_parse_error method={} chat_id={} thread_id={} snippet={}",
                        method,
                        key.chat_id,
                        key.thread_id,
                        snippet,
                    )
                raise

    def _rate_key(self, chat_id: int, thread_id: int | None) -> RateKey:
        return RateKey(chat_id=chat_id, thread_id=thread_id)

    async def _call_chat(
        self,
        method: str,
        chat_id: int,
        thread_id: int | None,
        func: Callable[..., Awaitable[Any]],
        **kwargs: Any,
    ) -> Any:
        key = self._rate_key(chat_id, thread_id)
        return await self._call(method, key, func, **kwargs)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        message_thread_id: int | None = None,
        **kwargs: Any,
    ):
        key = self._rate_key(chat_id, message_thread_id)
        return await self._call(
            "send_message",
            key,
            self.bot.send_message,
            chat_id=chat_id,
            text=text,
            message_thread_id=message_thread_id,
            **kwargs,
        )

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        message_thread_id: int | None = None,
        **kwargs: Any,
    ):
        safe_text = html_escape(text)
        kwargs.pop("parse_mode", None)
        key = self._rate_key(chat_id, message_thread_id)
        return await self._call(
            "send_text",
            key,
            self.bot.send_message,
            chat_id=chat_id,
            text=safe_text,
            message_thread_id=message_thread_id,
            parse_mode=None,
            **kwargs,
        )

    async def send_ephemeral_text(
        self,
        chat_id: int,
        text: str,
        *,
        ttl_sec: int,
        message_thread_id: int | None = None,
        **kwargs: Any,
    ):
        msg = await self.send_text(
            chat_id=chat_id,
            text=text,
            message_thread_id=message_thread_id,
            **kwargs,
        )
        await self.schedule_delete(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            thread_id=msg.message_thread_id,
            ttl_sec=ttl_sec,
        )
        return msg

    async def send_document(
        self,
        chat_id: int,
        document: Any,
        *,
        ttl_sec: int | None = None,
        message_thread_id: int | None = None,
        **kwargs: Any,
    ):
        key = self._rate_key(chat_id, message_thread_id)
        msg = await self._call(
            "send_document",
            key,
            self.bot.send_document,
            chat_id=chat_id,
            document=document,
            message_thread_id=message_thread_id,
            **kwargs,
        )
        if ttl_sec is not None:
            await self.schedule_delete(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                thread_id=msg.message_thread_id,
                ttl_sec=ttl_sec,
            )
        return msg

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        **kwargs: Any,
    ):
        key = self._rate_key(chat_id, thread_id)
        return await self._call(
            "edit_message_text",
            key,
            self.bot.edit_message_text,
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            **kwargs,
        )

    async def edit_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        thread_id: int | None = None,
        **kwargs: Any,
    ):
        safe_text = html_escape(text)
        kwargs.pop("parse_mode", None)
        key = self._rate_key(chat_id, thread_id)
        return await self._call(
            "edit_text",
            key,
            self.bot.edit_message_text,
            chat_id=chat_id,
            message_id=message_id,
            text=safe_text,
            parse_mode=None,
            **kwargs,
        )

    async def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        *,
        reply_markup: Any | None = None,
        thread_id: int | None = None,
        **kwargs: Any,
    ):
        key = self._rate_key(chat_id, thread_id)
        return await self._call(
            "edit_message_reply_markup",
            key,
            self.bot.edit_message_reply_markup,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
            **kwargs,
        )

    async def delete_message(
        self,
        chat_id: int,
        message_id: int,
        *,
        thread_id: int | None = None,
        **kwargs: Any,
    ):
        key = self._rate_key(chat_id, thread_id)
        return await self._call(
            "delete_message",
            key,
            self.bot.delete_message,
            chat_id=chat_id,
            message_id=message_id,
            **kwargs,
        )

    async def schedule_delete(
        self,
        *,
        chat_id: int,
        message_id: int,
        thread_id: int | None = None,
        ttl_sec: int | None = None,
        delete_at: float | None = None,
    ) -> None:
        if self.deletion_service is None:
            logger.debug(
                "delete_schedule_skipped chat_id={} message_id={} reason=no_service",
                chat_id,
                message_id,
            )
            return
        if delete_at is None:
            if ttl_sec is None:
                raise ValueError("ttl_sec or delete_at is required")
            delete_at = time.time() + float(ttl_sec)
        await self.deletion_service.schedule(
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            delete_at=delete_at,
        )

    async def answer(
        self,
        event: Message | CallbackQuery,
        text: str | None = None,
        **kwargs: Any,
    ):
        if isinstance(event, Message):
            return await self.send_text(
                chat_id=event.chat.id,
                message_thread_id=event.message_thread_id,
                text=text or "",
                **kwargs,
            )
        chat_id = 0
        thread_id = None
        if event.message:
            chat_id = event.message.chat.id
            thread_id = event.message.message_thread_id
        key = self._rate_key(chat_id, thread_id)
        return await self._call(
            "answer_callback_query",
            key,
            self.bot.answer_callback_query,
            callback_query_id=event.id,
            text=text,
            **kwargs,
        )

    async def reply(
        self,
        message: Message,
        text: str,
        **kwargs: Any,
    ):
        return await self.send_text(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=text,
            reply_to_message_id=message.message_id,
            **kwargs,
        )
