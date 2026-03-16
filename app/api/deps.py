from datetime import datetime, timezone
from typing import Literal

from fastapi import Header, HTTPException, Request
from loguru import logger

from app.db.database import AsyncSessionLocal
from app.db.models.tenant import Tenant
from app.db.repositories.tenant_repository import TenantRepository


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
    return str(request_id or "n/a")


def _log_auth_denied(
    request: Request,
    *,
    scope: Literal["ingest", "management"],
    reason: str,
    tenant_id: int | None = None,
) -> None:
    logger.warning(
        "api_auth_denied request_id={} method={} path={} scope={} reason={} tenant_id={}",
        _request_id(request),
        request.method,
        request.url.path,
        scope,
        reason,
        tenant_id,
    )


def _assert_subscription_active(tenant: Tenant) -> None:
    if tenant.subscription_until and tenant.subscription_until < datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="Subscription expired")


def _set_tenant_state(request: Request, tenant: Tenant, scope: Literal["ingest", "management"]) -> None:
    request.state.tenant = tenant
    request.state.tenant_id = tenant.id
    request.state.api_scope = scope


def _assert_not_browser_request(request: Request) -> None:
    # Browser cross-origin requests include Origin.
    # Ingest API is expected to be called server-to-server only.
    if request.headers.get("origin"):
        raise HTTPException(
            status_code=403,
            detail="Ingest API is server-to-server only. Use backend proxy or Tilda server webhook.",
        )


async def verify_ingest_api_key(request: Request, x_api_key: str = Header(...)) -> None:
    """
    Authorize ingest endpoints by tenant ingest key (`X-API-Key`).
    """
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_api_key(x_api_key)

    if not tenant:
        _log_auth_denied(request, scope="ingest", reason="invalid_api_key")
        raise HTTPException(status_code=401, detail="Invalid ingest API key")

    try:
        _assert_subscription_active(tenant)
    except HTTPException:
        _log_auth_denied(
            request,
            scope="ingest",
            reason="subscription_expired",
            tenant_id=tenant.id,
        )
        raise
    _set_tenant_state(request, tenant, "ingest")


async def verify_ingest_server_api_key(request: Request, x_api_key: str = Header(...)) -> None:
    await verify_ingest_api_key(request=request, x_api_key=x_api_key)
    try:
        _assert_not_browser_request(request)
    except HTTPException:
        _log_auth_denied(
            request,
            scope="ingest",
            reason="browser_origin_not_allowed",
            tenant_id=getattr(request.state, "tenant_id", None),
        )
        raise


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
        _log_auth_denied(request, scope="management", reason="invalid_management_api_key")
        raise HTTPException(status_code=401, detail="Invalid management API key")

    try:
        _assert_subscription_active(tenant)
    except HTTPException:
        _log_auth_denied(
            request,
            scope="management",
            reason="subscription_expired",
            tenant_id=tenant.id,
        )
        raise
    _set_tenant_state(request, tenant, "management")


# Backward compatibility alias for existing imports.
async def verify_api_key(request: Request, x_api_key: str = Header(...)) -> None:
    await verify_ingest_api_key(request=request, x_api_key=x_api_key)


async def get_current_sender(request: Request):
    return request.app.state.sender


async def get_current_bot(request: Request):
    return request.app.state.bot
