import asyncio
import os
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from starlette.requests import Request

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

from app.api import deps
from app.db.models.tenant import Tenant


class _FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeRepo:
    def __init__(self, *, ingest_tenant: Tenant | None = None, management_tenant: Tenant | None = None):
        self.ingest_tenant = ingest_tenant
        self.management_tenant = management_tenant

    async def get_by_api_key(self, api_key: str):
        if api_key == "ingest-key":
            return self.ingest_tenant
        return None

    async def get_by_management_api_key(self, api_key: str):
        if api_key == "management-key":
            return self.management_tenant
        return None


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def _active_tenant(tenant_id: int) -> Tenant:
    return Tenant(
        id=tenant_id,
        group_id=-1001,
        owner_tg_id=tenant_id + 1000,
        company_name=f"Tenant {tenant_id}",
        is_active=True,
        subscription_until=datetime.now(timezone.utc) + timedelta(days=1),
    )


def _expired_tenant(tenant_id: int) -> Tenant:
    return Tenant(
        id=tenant_id,
        group_id=-1001,
        owner_tg_id=tenant_id + 1000,
        company_name=f"Tenant {tenant_id}",
        is_active=True,
        subscription_until=datetime.now(timezone.utc) - timedelta(days=1),
    )


def test_verify_ingest_api_key_sets_ingest_scope(monkeypatch) -> None:
    tenant = _active_tenant(1)
    repo = _FakeRepo(ingest_tenant=tenant)

    monkeypatch.setattr(deps, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(deps, "TenantRepository", lambda session: repo)

    request = _request()
    asyncio.run(deps.verify_ingest_api_key(request=request, x_api_key="ingest-key"))

    assert request.state.tenant_id == tenant.id
    assert request.state.api_scope == "ingest"


def test_verify_management_api_key_sets_management_scope(monkeypatch) -> None:
    tenant = _active_tenant(2)
    repo = _FakeRepo(management_tenant=tenant)

    monkeypatch.setattr(deps, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(deps, "TenantRepository", lambda session: repo)

    request = _request()
    asyncio.run(
        deps.verify_management_api_key(
            request=request,
            x_management_api_key="management-key",
        )
    )

    assert request.state.tenant_id == tenant.id
    assert request.state.api_scope == "management"


def test_ingest_key_cannot_pass_management_dependency(monkeypatch) -> None:
    tenant = _active_tenant(3)
    repo = _FakeRepo(ingest_tenant=tenant, management_tenant=None)

    monkeypatch.setattr(deps, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(deps, "TenantRepository", lambda session: repo)

    request = _request()
    try:
        asyncio.run(
            deps.verify_management_api_key(
                request=request,
                x_management_api_key="ingest-key",
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 401
        assert "management" in exc.detail.lower()
    else:
        raise AssertionError("Expected HTTPException for invalid management key")


def test_expired_subscription_rejected_for_both_scopes(monkeypatch) -> None:
    tenant = _expired_tenant(4)
    repo = _FakeRepo(ingest_tenant=tenant, management_tenant=tenant)

    monkeypatch.setattr(deps, "AsyncSessionLocal", lambda: _FakeSessionContext())
    monkeypatch.setattr(deps, "TenantRepository", lambda session: repo)

    request_ingest = _request()
    try:
        asyncio.run(deps.verify_ingest_api_key(request=request_ingest, x_api_key="ingest-key"))
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Expected HTTPException for expired ingest key")

    request_management = _request()
    try:
        asyncio.run(
            deps.verify_management_api_key(
                request=request_management,
                x_management_api_key="management-key",
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Expected HTTPException for expired management key")
