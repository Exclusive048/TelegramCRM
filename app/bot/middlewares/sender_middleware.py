from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Dispatcher
from aiogram.types import TelegramObject

from app.telegram.safe_sender import TelegramSafeSender


class SenderMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        dp = data.get("dispatcher")
        if isinstance(dp, Dispatcher):
            sender = dp.workflow_data.get("sender")
            if sender is not None:
                data["sender"] = sender
        return await handler(event, data)
