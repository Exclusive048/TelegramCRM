from aiogram import Router

from app.bot.handlers import cabinet, lead_callbacks, panel, setup


def build_crm_router() -> Router:
    router = Router()
    router.include_router(lead_callbacks.router)
    router.include_router(setup.router)
    router.include_router(cabinet.router)
    router.include_router(panel.router)
    return router
