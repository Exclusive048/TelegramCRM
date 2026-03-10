from __future__ import annotations

import asyncio
import os
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from app.telegram.safe_sender import ChatRateLimiter, TelegramSafeSender


def _ensure_env():
    os.environ.setdefault("BOT_TOKEN", "123:TEST_TOKEN")
    os.environ.setdefault("CRM_GROUP_ID", "1")
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
    os.environ.setdefault("PUBLIC_DOMAIN", "example.com")
    os.environ.setdefault("API_SECRET_KEY", "dev")


async def _close_bot(bot: Bot):
    await bot.session.close()


def main() -> int:
    _ensure_env()

    from app.core.config import settings  # noqa: WPS433
    from app.bootstrap import create_app  # noqa: WPS433

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    sender = TelegramSafeSender(bot, limiter=ChatRateLimiter(min_delay_sec=1.1))
    app = create_app(bot, sender, redis_url=settings.redis_url)

    if not app:
        print("Smoke check failed: app is None")
        asyncio.run(_close_bot(bot))
        return 1

    asyncio.run(_close_bot(bot))
    print("Smoke check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
