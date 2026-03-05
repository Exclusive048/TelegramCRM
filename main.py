import asyncio

from loguru import logger

from app.bootstrap import create_app, init_sender, init_storage, start_bot_with_retry
from app.entrypoints.api import main as api_main

__all__ = ["create_app", "init_sender", "init_storage", "start_bot_with_retry", "main"]


async def main() -> None:
    logger.warning(
        "Legacy entrypoint main.py. Use app.entrypoints.api, app.entrypoints.crm_bot, "
        "app.entrypoints.master_bot instead."
    )
    await api_main()


if __name__ == "__main__":
    asyncio.run(main())
