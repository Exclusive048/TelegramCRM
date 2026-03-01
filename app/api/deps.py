from fastapi import Header, HTTPException, Request
from app.core.config import settings
import secrets

async def verify_api_key(x_api_key: str = Header(...)):
    """Проверяет X-API-Key в заголовке каждого запроса"""
    if not secrets.compare_digest(x_api_key, settings.api_secret_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


async def get_current_bot(request: Request):
    """Достаёт экземпляр бота из app.state (прокидывается при старте)"""
    return request.app.state.bot


async def get_current_sender(request: Request):
    """Достаёт safe sender из app.state (прокидывается при старте)"""
    return request.app.state.sender
