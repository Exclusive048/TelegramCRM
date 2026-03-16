from datetime import datetime, timezone
from typing import Literal

from fastapi import Header, HTTPException, Request

from app.db.database import AsyncSessionLocal
from app.db.models.tenant import Tenant
from app.db.repositories.tenant_repository import TenantRepository


def _assert_subscription_active(tenant: Tenant) -> None:
    if tenant.subscription_until and tenant.subscription_until < datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="Subscription expired")


def _set_tenant_state(request: Request, tenant: Tenant, scope: Literal["ingest", "management"]) -> None:
    request.state.tenant = tenant
    request.state.tenant_id = tenant.id
    request.state.api_scope = scope


async def verify_ingest_api_key(request: Request, x_api_key: str = Header(...)) -> None:
    """
    Authorize ingest endpoints by tenant ingest key (`X-API-Key`).
    """
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_api_key(x_api_key)

    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid ingest API key")

    _assert_subscription_active(tenant)
    _set_tenant_state(request, tenant, "ingest")


async def verify_management_api_key(
    request: Request,
    x_management_api_key: str = Header(..., alias="X-Management-API-Key"),
) -> None:
    """
    Authorize management endpoints by tenant management key (`X-Management-API-Key`).
    """
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_management_api_key(x_management_api_key)

    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid management API key")

    _assert_subscription_active(tenant)
    _set_tenant_state(request, tenant, "management")


# Backward compatibility alias for existing imports.
async def verify_api_key(request: Request, x_api_key: str = Header(...)) -> None:
    await verify_ingest_api_key(request=request, x_api_key=x_api_key)


async def get_current_sender(request: Request):
    return request.app.state.sender


async def get_current_bot(request: Request):
    return request.app.state.bot