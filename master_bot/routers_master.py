from aiogram import Router

from master_bot.admin import router as admin_router
from master_bot.handlers import router as master_router


def build_master_router() -> Router:
    router = Router(name="master.root")
    
    router.include_router(master_router)
    router.include_router(admin_router)
    return router
