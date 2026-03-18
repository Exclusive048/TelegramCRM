from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Dispatcher
from aiogram.types import TelegramObject

from app.bot.diagnostics import TG_MIDDLEWARE_ENTER, TG_MIDDLEWARE_EXIT, emit_tg_event


class SenderMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        emit_tg_event(TG_MIDDLEWARE_ENTER, middleware="sender")
        dp = data.get("dispatcher")
        sender_injected = False
        if isinstance(dp, Dispatcher):
            sender = dp.workflow_data.get("sender")
            if sender is not None:
                data["sender"] = sender
                sender_injected = True
        try:
            return await handler(event, data)
        finally:
            emit_tg_event(
                TG_MIDDLEWARE_EXIT,
                middleware="sender",
                outcome="pass",
                sender_injected=sender_injected,
            )
