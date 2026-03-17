from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import limiter
from app.api.deps import (
    get_current_sender,
    verify_ingest_server_api_key,
    verify_management_api_key,
)
from app.api.schemas.lead_schemas import (
    CreateLeadResponse,
    LeadCommentRequest,
    LeadCreateRequest,
    LeadListResponse,
    LeadResponse,
    LeadUpdateRequest,
    OkResponse,
)
from app.db.database import AsyncSessionLocal, get_db
from app.db.models.lead import LeadStatus
from app.db.repositories.lead_repository import LeadRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.services.lead_service import LeadService

router = APIRouter(prefix="/leads", tags=["Leads"])
MAX_EXTRA_KEYS = 20
MAX_LOGGED_PAYLOAD_KEYS = 25
_SENSITIVE_LOG_FIELDS = {
    "name",
    "phone",
    "email",
    "comment",
    "notes",
    "note",
    "service",
    "extra",
}

# РџРѕР»СЏ РєРѕС‚РѕСЂС‹Рµ РёСЃРєР»СЋС‡Р°РµРј РёР· extra (С‚РµС…РЅРёС‡РµСЃРєРёРµ / РјСѓСЃРѕСЂ)
_SKIP_EXTRA = {
    "Name", "name", "NAME",
    "Phone", "phone", "PHONE",
    "Email", "email", "EMAIL",
    "Comment", "comment", "MESSAGE", "Message", "message",
    "Service", "service",
    "utm_campaign", "UTM_CAMPAIGN",
    "utm_source", "utm_medium",
    "formid", "formname", "tranid",
    "COOKIES", "$$_headers",
    "assent",
}


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
    return str(request_id or "n/a")


def _payload_shape(payload: dict[str, Any]) -> dict[str, Any]:
    keys = sorted({str(key).strip()[:64] for key in payload if str(key).strip()})
    normalized_lower = {key.lower() for key in keys}
    sensitive_keys = sorted(normalized_lower.intersection(_SENSITIVE_LOG_FIELDS))
    return {
        "payload_key_count": len(keys),
        "payload_keys": keys[:MAX_LOGGED_PAYLOAD_KEYS],
        "payload_keys_truncated": len(keys) > MAX_LOGGED_PAYLOAD_KEYS,
        "contains_sensitive_keys": sensitive_keys,
    }


def _safe_lead_flags(payload: dict[str, Any]) -> dict[str, Any]:
    extra = payload.get("extra")
    return {
        "has_name": bool(payload.get("name")),
        "has_phone": bool(payload.get("phone")),
        "has_email": bool(payload.get("email")),
        "has_comment": bool(payload.get("comment")),
        "has_service": bool(payload.get("service")),
        "extra_key_count": len(extra) if isinstance(extra, dict) else 0,
    }


def _pick(data: dict, *keys: str, default: str = "") -> str:
    """Р‘РµСЂС‘С‚ РїРµСЂРІРѕРµ РЅРµРїСѓСЃС‚РѕРµ Р·РЅР°С‡РµРЅРёРµ РёР· СЃРїРёСЃРєР° РєР»СЋС‡РµР№."""
    for k in keys:
        v = data.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return default


def _parse_tilda(data: dict) -> dict:
    """
    РЈРЅРёРІРµСЂСЃР°Р»СЊРЅС‹Р№ РїР°СЂСЃРµСЂ Tilda-С„РѕСЂРј.
    РџРѕРґРґРµСЂР¶РёРІР°РµС‚ СЃС‚Р°РЅРґР°СЂС‚РЅС‹Рµ РїРѕР»СЏ (Name/Phone) Рё РЅРµСЃС‚Р°РЅРґР°СЂС‚РЅС‹Рµ
    (messenger-id, program, city Рё С‚.Рґ.).
    """
    # в”Ђв”Ђ РРјСЏ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    name = _pick(data, "Name", "name", "NAME", "Р¤РРћ", "fio", "fullname")
    if not name:
        # РЎРѕР±РёСЂР°РµРј РёР· С‡Р°СЃС‚РµР№
        parts = [
            _pick(data, "firstname", "Firstname", "first_name"),
            _pick(data, "lastname", "Lastname", "last_name"),
        ]
        name = " ".join(p for p in parts if p).strip()
    if not name:
        name = _pick(data, "formname", "program", default="Р—Р°СЏРІРєР° СЃ СЃР°Р№С‚Р°")

    # в”Ђв”Ђ РўРµР»РµС„РѕРЅ / РєРѕРЅС‚Р°РєС‚ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    phone = _pick(data, "Phone", "phone", "PHONE", "tel", "Tel", "telephone")
    if not phone:
        messenger_id = _pick(data, "messenger-id", "messenger_id")
        messenger_type = _pick(data, "messenger-type", "messenger_type")
        if messenger_id:
            prefix = f"{messenger_type}: " if messenger_type else ""
            phone = f"{prefix}{messenger_id}"
    if not phone:
        phone = _pick(data, "email", "Email", "EMAIL", default="вЂ”")

    # в”Ђв”Ђ Email в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    email = _pick(data, "email", "Email", "EMAIL") or None

    # в”Ђв”Ђ РЈСЃР»СѓРіР° в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    service = _pick(data, "Service", "service", "program", "Program") or None
    if not service:
        formname = _pick(data, "formname")
        if formname:
            service = formname

    # в”Ђв”Ђ РљРѕРјРјРµРЅС‚Р°СЂРёР№ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    comment_parts = []

    explicit_comment = _pick(data, "Comment", "comment", "Message", "message", "Messages")
    if explicit_comment:
        comment_parts.append(explicit_comment)

    # Р”РѕР±Р°РІР»СЏРµРј РіРµРѕ/РІСЂРµРјСЏ РµСЃР»Рё РµСЃС‚СЊ
    for field, label in [
        ("city", "Р“РѕСЂРѕРґ"),
        ("City", "Р“РѕСЂРѕРґ"),
        ("country", "РЎС‚СЂР°РЅР°"),
        ("location", "Р›РѕРєР°С†РёСЏ"),
        ("time", "Р’СЂРµРјСЏ"),
    ]:
        val = data.get(field)
        if val and str(val).strip():
            comment_parts.append(f"{label}: {val}")

    if not comment_parts:
        comment_parts.append("Р—Р°СЏРІРєР° СЃ Tilda")

    comment = " | ".join(comment_parts)

    # в”Ђв”Ђ UTM в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    utm_campaign = _pick(data, "utm_campaign", "UTM_CAMPAIGN") or None
    utm_source = _pick(data, "utm_source", "utm_medium") or None

    # в”Ђв”Ђ Extra вЂ” РІСЃС‘ РѕСЃС‚Р°Р»СЊРЅРѕРµ РїРѕР»РµР·РЅРѕРµ в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    extra: dict[str, str] = {}
    for k, v in list(data.items()):
        if k in _SKIP_EXTRA:
            continue
        if k.startswith("$$") or k == "COOKIES":
            continue
        val = str(v).strip()
        if val and len(extra) < MAX_EXTRA_KEYS:
            extra[k] = val[:500]

    return {
        "name": name[:255],
        "phone": phone[:50],
        "email": email,
        "source": "tilda",
        "comment": comment[:1000],
        "service": service[:255] if service else None,
        "utm_campaign": utm_campaign,
        "utm_source": utm_source,
        "extra": extra or None,
    }



def _quota_limit_error(limit: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=(
            f"Достигнут лимит {limit} заявок в месяц. "
            "Перейдите на тариф Базовый для снятия ограничений."
        ),
    )


async def _create_lead_atomic(
    *,
    tenant,
    sender,
    payload: dict,
    request_id: str,
    endpoint: str,
    source: str,
):
    tenant_id = tenant.id if tenant else None
    group_id = tenant.group_id if tenant else 0

    async with AsyncSessionLocal() as session:
        try:
            if tenant and tenant.max_leads_per_month != -1:
                tenant_repo = TenantRepository(session)
                allowed, _, limit = await tenant_repo.try_reserve_monthly_lead_quota(tenant.id)
                if not allowed:
                    raise _quota_limit_error(limit)

            repo = LeadRepository(session)
            service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
            lead = await service.create_lead(payload)
            await session.commit()
            return lead
        except HTTPException as exc:
            await session.rollback()
            if exc.status_code == 429:
                logger.warning(
                    "ingest_quota_denied request_id={} tenant_id={} endpoint={} source={} status_code={}",
                    request_id,
                    tenant_id,
                    endpoint,
                    source,
                    exc.status_code,
                )
            raise
        except Exception:
            await session.rollback()
            logger.exception(
                "ingest_create_failed request_id={} tenant_id={} endpoint={} source={} reason=unexpected_exception",
                request_id,
                tenant_id,
                endpoint,
                source,
            )
            raise
# в”Ђв”Ђ POST /leads в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.post("", response_model=CreateLeadResponse, status_code=201)
@limiter.limit("60/minute")
async def create_lead(
    body: LeadCreateRequest,
    request: Request,
    _: None = Depends(verify_ingest_server_api_key),
    sender=Depends(get_current_sender),
):
    tenant = request.state.tenant
    request_id = _request_id(request)
    tenant_id = tenant.id if tenant else None
    payload = body.model_dump(exclude_none=True)
    source = str(payload.get("source") or "api")
    payload_shape = _payload_shape(payload)
    logger.info(
        "ingest_request_received request_id={} tenant_id={} endpoint={} source={} payload_key_count={} payload_keys={} payload_keys_truncated={}",
        request_id,
        tenant_id,
        "/api/v1/leads",
        source,
        payload_shape["payload_key_count"],
        payload_shape["payload_keys"],
        payload_shape["payload_keys_truncated"],
    )
    if tenant and not tenant.group_id:
        logger.warning(
            "ingest_rejected request_id={} tenant_id={} endpoint={} source={} reason=group_not_configured",
            request_id,
            tenant_id,
            "/api/v1/leads",
            source,
        )
        return JSONResponse(
            status_code=409,
            content={"error": "group_not_configured"},
        )
    lead = await _create_lead_atomic(
        tenant=tenant,
        sender=sender,
        payload=payload,
        request_id=request_id,
        endpoint="/api/v1/leads",
        source=source,
    )
    logger.info(
        "ingest_create_success request_id={} tenant_id={} endpoint={} source={} lead_id={}",
        request_id,
        tenant_id,
        "/api/v1/leads",
        source,
        lead.id,
    )
    return CreateLeadResponse(lead_id=lead.id, tg_message_id=lead.tg_message_id)

# в”Ђв”Ђ POST /leads/tilda вЂ” webhook РґР»СЏ Tilda в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.post("/tilda", response_model=OkResponse, status_code=201, tags=["Integrations"])
@limiter.limit("60/minute")
async def tilda_webhook(
    request: Request,
    _: None = Depends(verify_ingest_server_api_key),
    sender=Depends(get_current_sender),
):
    """
    Tilda webhook endpoint.
    Accepts form-data or JSON and normalizes it into lead payload.
    """
    content_type = request.headers.get("content-type", "")
    request_id = _request_id(request)
    tenant = request.state.tenant
    tenant_id = tenant.id if tenant else None
    endpoint = "/api/v1/leads/tilda"
    source = "tilda"

    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception as exc:
            logger.warning(
                "ingest_payload_parse_error request_id={} tenant_id={} endpoint={} source={} content_type={} error_type={}",
                request_id,
                tenant_id,
                endpoint,
                source,
                content_type,
                type(exc).__name__,
            )
            data = {}
    else:
        # form-data (standard Tilda format)
        form = await request.form()
        data = dict(form)

    payload_shape = _payload_shape(data)
    logger.info(
        "ingest_request_received request_id={} tenant_id={} endpoint={} source={} payload_key_count={} payload_keys={} payload_keys_truncated={}",
        request_id,
        tenant_id,
        endpoint,
        source,
        payload_shape["payload_key_count"],
        payload_shape["payload_keys"],
        payload_shape["payload_keys_truncated"],
    )

    lead_data = _parse_tilda(data)
    lead_flags = _safe_lead_flags(lead_data)
    logger.info(
        "tilda_payload_parsed request_id={} tenant_id={} endpoint={} source={} has_name={} has_phone={} has_email={} has_comment={} has_service={} extra_key_count={} contains_sensitive_keys={}",
        request_id,
        tenant_id,
        endpoint,
        source,
        lead_flags["has_name"],
        lead_flags["has_phone"],
        lead_flags["has_email"],
        lead_flags["has_comment"],
        lead_flags["has_service"],
        lead_flags["extra_key_count"],
        payload_shape["contains_sensitive_keys"],
    )

    lead = await _create_lead_atomic(
        tenant=tenant,
        sender=sender,
        payload={k: v for k, v in lead_data.items() if v is not None},
        request_id=request_id,
        endpoint=endpoint,
        source=source,
    )
    logger.info(
        "ingest_create_success request_id={} tenant_id={} endpoint={} source={} lead_id={}",
        request_id,
        tenant_id,
        endpoint,
        source,
        lead.id,
    )
    return OkResponse()


# в”Ђв”Ђ GET /leads в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.get("", response_model=LeadListResponse)
async def get_leads(
    request: Request,
    status: LeadStatus | None = Query(None),
    source: str | None = Query(None),
    manager_id: int | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_management_api_key),
):
    tenant = request.state.tenant
    tenant_id = tenant.id if tenant else None
    repo = LeadRepository(db)
    leads, total = await repo.get_list(
        status=status,
        source=source,
        manager_id=manager_id,
        date_from=date_from,
        date_to=date_to,
        search=search,
        page=page,
        per_page=per_page,
        tenant_id=tenant_id,
    )
    return LeadListResponse(
        total=total,
        page=page,
        per_page=per_page,
        data=[LeadResponse.model_validate(l) for l in leads],
    )


# в”Ђв”Ђ GET /leads/{id} в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_management_api_key),
):
    tenant = request.state.tenant
    tenant_id = tenant.id if tenant else None
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id, tenant_id=tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return LeadResponse.model_validate(lead)


# в”Ђв”Ђ PATCH /leads/{id} в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.patch("/{lead_id}", response_model=OkResponse)
async def update_lead(
    lead_id: int,
    body: LeadUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_management_api_key),
    sender=Depends(get_current_sender),
):
    tenant = request.state.tenant
    tenant_id = tenant.id if tenant else None
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id, tenant_id=tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    group_id = tenant.group_id if tenant else 0
    service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
    manager_tg_id = body.manager_tg_id or 0
    result = None
    if body.status == LeadStatus.IN_PROGRESS:
        result = await service.take_in_progress(lead_id, manager_tg_id, None)
    elif body.status == LeadStatus.PAID:
        result = await service.mark_paid(lead_id, manager_tg_id, body.amount, None)
    elif body.status == LeadStatus.SUCCESS:
        result = await service.mark_success(lead_id, manager_tg_id, None)
    elif body.status == LeadStatus.REJECTED:
        result = await service.reject_lead(lead_id, manager_tg_id, body.reject_reason or "", None)
    if not result:
        return JSONResponse(status_code=409, content={"error": "invalid_transition"})

    return OkResponse()

# в”Ђв”Ђ POST /leads/{id}/comment в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ

@router.post("/{lead_id}/comment", response_model=OkResponse, status_code=201)
async def add_comment(
    lead_id: int,
    body: LeadCommentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_management_api_key),
    sender=Depends(get_current_sender),
):
    tenant = request.state.tenant
    tenant_id = tenant.id if tenant else None
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id, tenant_id=tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    group_id = tenant.group_id if tenant else 0
    service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
    await service.add_comment(
        lead_id=lead_id,
        text=body.text,
        author=body.author,
        target_ref=None,
    )
    return OkResponse()

