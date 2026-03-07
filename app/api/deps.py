from fastapi import Header, HTTPException, Request
from app.db.database import AsyncSessionLocal
from app.db.repositories.tenant_repository import TenantRepository


async def verify_api_key(request: Request, x_api_key: str = Header(...)):
    """
    Аутентификация по per-tenant API ключу.
    Ключ генерируется при активации подписки и хранится в tenants.api_key.
    Глобальный API_SECRET_KEY удалён — каждый клиент имеет свой ключ.
    """
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_api_key(x_api_key)

    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Проверить что подписка активна
    from datetime import datetime, timezone
    if tenant.subscription_until and tenant.subscription_until < datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="Subscription expired")

    request.state.tenant = tenant
    request.state.tenant_id = tenant.id


async def get_current_sender(request: Request):
    return request.app.state.sender


async def get_current_bot(request: Request):
    return request.app.state.bot
