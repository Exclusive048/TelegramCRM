from __future__ import annotations

import asyncio
import os
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from app.telegram.safe_sender import ChatRateLimiter, TelegramSafeSender


def _set_if_blank(name: str, value: str) -> None:
    current = os.getenv(name)
    if current is None or not current.strip():
        os.environ[name] = value


def _ensure_env():
    _set_if_blank("BOT_TOKEN", "123:TEST_TOKEN")
    _set_if_blank("CRM_GROUP_ID", "1")
    _set_if_blank("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
    _set_if_blank("PUBLIC_DOMAIN", "example.com")
    _set_if_blank("MASTER_ADMIN_TG_ID", "0")
    _set_if_blank("API_SECRET_KEY", "dev")


async def _close_bot(bot: Bot):
    await bot.session.close()


def main() -> int:
    _ensure_env()

    from app.core.config import settings  # noqa: WPS433
    from app.bootstrap import create_app  # noqa: WPS433

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    sender = TelegramSafeSender(bot, limiter=ChatRateLimiter(min_delay_sec=1.1))
    app = create_app(
        bot,
        sender,
        redis_url=settings.redis_url,
        use_redis=settings.use_redis,
    )

    if not app:
        print("Smoke check failed: app is None")
        asyncio.run(_close_bot(bot))
        return 1

    asyncio.run(_close_bot(bot))
    print("Smoke check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
