import asyncio
import json
import os
import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.requests import Request

os.environ.setdefault("MASTER_ADMIN_TG_ID", "1")

_fake_rate_limit = types.ModuleType("app.api.rate_limit")


class _FakeLimiter:
    def limit(self, *args, **kwargs):
        def _decorator(func):
            return func

        return _decorator


_fake_rate_limit.limiter = _FakeLimiter()
sys.modules["app.api.rate_limit"] = _fake_rate_limit

from app.api.routes import leads
from app.api.schemas.lead_schemas import LeadUpdateRequest, OkResponse
from app.db.models.lead import LeadStatus


def _request(*, tenant_id: int = 77, group_id: int = -100500) -> Request:
    request = Request({"type": "http", "method": "PATCH", "path": "/api/v1/leads/1", "headers": []})
    request.state.tenant = SimpleNamespace(id=tenant_id, group_id=group_id)
    return request


def test_lead_patch_schema_is_status_only_and_forbids_extra_fields() -> None:
    assert set(LeadUpdateRequest.model_fields.keys()) == {
        "status",
        "manager_tg_id",
        "reject_reason",
        "amount",
    }
    assert LeadUpdateRequest.model_fields["status"].is_required()
    assert LeadUpdateRequest.model_config.get("extra") == "forbid"


def test_lead_patch_schema_rejects_unsupported_partial_fields() -> None:
    with pytest.raises(ValidationError):
        LeadUpdateRequest(status=LeadStatus.PAID, service="new-service")


def test_lead_patch_schema_requires_status() -> None:
    with pytest.raises(ValidationError):
        LeadUpdateRequest()


def test_update_lead_returns_404_when_lead_not_found(monkeypatch) -> None:
    class _FakeRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_id(self, lead_id: int, tenant_id: int | None = None):
            return None

    monkeypatch.setattr(leads, "LeadRepository", _FakeRepo)

    body = LeadUpdateRequest(status=LeadStatus.PAID)
    request = _request()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            leads.update_lead(
                lead_id=1,
                body=body,
                request=request,
                db=object(),
                _=None,
                sender=object(),
            )
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Lead not found"


def test_update_lead_returns_409_for_unsupported_status_transition(monkeypatch) -> None:
    class _FakeRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_id(self, lead_id: int, tenant_id: int | None = None):
            return {"id": lead_id}

    calls = {"method": None}

    class _FakeService:
        def __init__(self, repo, sender, group_id: int, tenant_id: int | None = None):
            self.repo = repo
            self.sender = sender
            self.group_id = group_id
            self.tenant_id = tenant_id

        async def take_in_progress(self, lead_id, manager_tg_id, source_ref):
            calls["method"] = "take_in_progress"
            return {"id": lead_id}

        async def mark_paid(self, lead_id, manager_tg_id, amount, source_ref):
            calls["method"] = "mark_paid"
            return {"id": lead_id}

        async def mark_success(self, lead_id, manager_tg_id, source_ref):
            calls["method"] = "mark_success"
            return {"id": lead_id}

        async def reject_lead(self, lead_id, manager_tg_id, reason, source_ref):
            calls["method"] = "reject_lead"
            return {"id": lead_id}

    monkeypatch.setattr(leads, "LeadRepository", _FakeRepo)
    monkeypatch.setattr(leads, "LeadService", _FakeService)

    body = LeadUpdateRequest(status=LeadStatus.NEW)
    response = asyncio.run(
        leads.update_lead(
            lead_id=1,
            body=body,
            request=_request(),
            db=object(),
            _=None,
            sender=object(),
        )
    )
    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert json.loads(response.body) == {"error": "invalid_transition"}
    assert calls["method"] is None


def test_update_lead_runs_status_flow_for_supported_transition(monkeypatch) -> None:
    class _FakeRepo:
        def __init__(self, db):
            self.db = db

        async def get_by_id(self, lead_id: int, tenant_id: int | None = None):
            return {"id": lead_id}

    captured: dict[str, object] = {}

    class _FakeService:
        def __init__(self, repo, sender, group_id: int, tenant_id: int | None = None):
            captured["group_id"] = group_id
            captured["tenant_id"] = tenant_id

        async def take_in_progress(self, lead_id, manager_tg_id, source_ref):
            return None

        async def mark_paid(self, lead_id, manager_tg_id, amount, source_ref):
            captured["lead_id"] = lead_id
            captured["manager_tg_id"] = manager_tg_id
            captured["amount"] = amount
            captured["source_ref"] = source_ref
            return {"id": lead_id}

        async def mark_success(self, lead_id, manager_tg_id, source_ref):
            return None

        async def reject_lead(self, lead_id, manager_tg_id, reason, source_ref):
            return None

    monkeypatch.setattr(leads, "LeadRepository", _FakeRepo)
    monkeypatch.setattr(leads, "LeadService", _FakeService)

    body = LeadUpdateRequest(status=LeadStatus.PAID, manager_tg_id=12345, amount=999.0)
    response = asyncio.run(
        leads.update_lead(
            lead_id=1,
            body=body,
            request=_request(tenant_id=88, group_id=-100700),
            db=object(),
            _=None,
            sender=object(),
        )
    )
    assert isinstance(response, OkResponse)
    assert response.status == "ok"
    assert captured["group_id"] == -100700
    assert captured["tenant_id"] == 88
    assert captured["lead_id"] == 1
    assert captured["manager_tg_id"] == 12345
    assert captured["amount"] == 999.0
    assert captured["source_ref"] is None
