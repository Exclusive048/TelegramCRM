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

# Поля которые исключаем из extra (технические / мусор)
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
_CREATE_LEAD_KNOWN_KEYS = frozenset(
    {
        "name",
        "phone",
        "email",
        "source",
        "comment",
        "service",
        "amount",
        "utm_campaign",
        "utm_source",
        "manager_id",
        "extra",
    }
)
_CREATE_LEAD_SHAPE_FLAG_KEYS = (
    "name",
    "phone",
    "email",
    "source",
    "comment",
    "service",
    "utm_campaign",
    "utm_source",
    "extra",
)
_TILDA_KNOWN_INPUT_KEYS = frozenset(
    {key.lower() for key in _SKIP_EXTRA}
    | {
        "firstname",
        "lastname",
        "first_name",
        "last_name",
        "fio",
        "fullname",
        "messenger-id",
        "messenger_id",
        "messenger-type",
        "messenger_type",
        "city",
        "country",
        "location",
        "time",
        "messages",
        "telephone",
        "tel",
    }
)
_TILDA_SHAPE_FLAG_KEYS = (
    "name",
    "phone",
    "email",
    "comment",
    "service",
    "program",
    "utm_campaign",
    "utm_source",
    "utm_medium",
    "messenger-id",
)


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")
    return str(request_id or "n/a")


def _payload_shape(
    payload: dict[str, Any],
    *,
    known_keys: set[str] | frozenset[str],
    flag_keys: tuple[str, ...],
) -> dict[str, Any]:
    normalized_keys = {
        str(key).strip().lower()[:64] for key in payload if str(key).strip()
    }
    known_present = normalized_keys.intersection(known_keys)
    unknown_key_count = len(normalized_keys - known_keys)
    sensitive_known_keys = known_present.intersection(_SENSITIVE_LOG_FIELDS)
    known_key_flags = {f"has_{key}": key in known_present for key in flag_keys}
    return {
        "payload_key_count": len(normalized_keys),
        "known_key_count": len(known_present),
        "unknown_key_count": unknown_key_count,
        "contains_unexpected_keys": unknown_key_count > 0,
        "known_key_flags": known_key_flags,
        "sensitive_known_key_count": len(sensitive_known_keys),
        "has_sensitive_known_keys": bool(sensitive_known_keys),
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
    """Берёт первое непустое значение из списка ключей."""
    for k in keys:
        v = data.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return default


def _parse_tilda(data: dict) -> dict:
    """
    Универсальный парсер Tilda-форм.
    Поддерживает стандартные поля (Name/Phone) и нестандартные
    (messenger-id, program, city Рё С‚.Рґ.).
    """
    # ── Имя ───────────────────────────────────────────────────────────────────
    name = _pick(data, "Name", "name", "NAME", "ФИО", "fio", "fullname")
    if not name:
        # Собираем из частей
        parts = [
            _pick(data, "firstname", "Firstname", "first_name"),
            _pick(data, "lastname", "Lastname", "last_name"),
        ]
        name = " ".join(p for p in parts if p).strip()
    if not name:
        name = _pick(data, "formname", "program", default="Заявка с сайта")

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

    # ── Услуга ────────────────────────────────────────────────────────────────
    service = _pick(data, "Service", "service", "program", "Program") or None
    if not service:
        formname = _pick(data, "formname")
        if formname:
            service = formname

    # ── Комментарий ───────────────────────────────────────────────────────────
    comment_parts = []

    explicit_comment = _pick(data, "Comment", "comment", "Message", "message", "Messages")
    if explicit_comment:
        comment_parts.append(explicit_comment)

    # Добавляем гео/время если есть
    for field, label in [
        ("city", "Р“РѕСЂРѕРґ"),
        ("City", "Р“РѕСЂРѕРґ"),
        ("country", "РЎС‚СЂР°РЅР°"),
        ("location", "Локация"),
        ("time", "Время"),
    ]:
        val = data.get(field)
        if val and str(val).strip():
            comment_parts.append(f"{label}: {val}")

    if not comment_parts:
        comment_parts.append("Заявка с Tilda")

    comment = " | ".join(comment_parts)

    # в”Ђв”Ђ UTM в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    utm_campaign = _pick(data, "utm_campaign", "UTM_CAMPAIGN") or None
    utm_source = _pick(data, "utm_source", "utm_medium") or None

    # ── Extra — всё остальное полезное ────────────────────────────────────────
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
    lead = None

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
    if lead is None:
        raise RuntimeError("Lead was not created")

    # Post-commit side-effect: Telegram card publishing must not run before lead commit.
    if tenant_id is not None and group_id:
        async with AsyncSessionLocal() as post_session:
            post_repo = LeadRepository(post_session)
            post_service = LeadService(
                post_repo,
                sender,
                group_id=group_id,
                tenant_id=tenant_id,
            )
            try:
                tg_message_id = await post_service.sync_new_lead_card(lead.id)
                await post_session.commit()
                if tg_message_id is not None:
                    lead.tg_message_id = tg_message_id
            except Exception:
                await post_session.rollback()
                logger.exception(
                    "ingest_post_commit_sync_failed request_id={} tenant_id={} endpoint={} source={} lead_id={} reason=telegram_side_effect_failed",
                    request_id,
                    tenant_id,
                    endpoint,
                    source,
                    lead.id,
                )
    return lead


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
    payload_shape = _payload_shape(
        payload,
        known_keys=_CREATE_LEAD_KNOWN_KEYS,
        flag_keys=_CREATE_LEAD_SHAPE_FLAG_KEYS,
    )
    logger.info(
        "ingest_request_received request_id={} tenant_id={} endpoint={} source={} payload_key_count={} known_key_count={} unknown_key_count={} contains_unexpected_keys={} known_key_flags={}",
        request_id,
        tenant_id,
        "/api/v1/leads",
        source,
        payload_shape["payload_key_count"],
        payload_shape["known_key_count"],
        payload_shape["unknown_key_count"],
        payload_shape["contains_unexpected_keys"],
        payload_shape["known_key_flags"],
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

# ── POST /leads/tilda — webhook для Tilda ─────────────────────────────────────

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

    payload_shape = _payload_shape(
        data,
        known_keys=_TILDA_KNOWN_INPUT_KEYS,
        flag_keys=_TILDA_SHAPE_FLAG_KEYS,
    )
    logger.info(
        "ingest_request_received request_id={} tenant_id={} endpoint={} source={} payload_key_count={} known_key_count={} unknown_key_count={} contains_unexpected_keys={} known_key_flags={}",
        request_id,
        tenant_id,
        endpoint,
        source,
        payload_shape["payload_key_count"],
        payload_shape["known_key_count"],
        payload_shape["unknown_key_count"],
        payload_shape["contains_unexpected_keys"],
        payload_shape["known_key_flags"],
    )

    lead_data = _parse_tilda(data)
    lead_flags = _safe_lead_flags(lead_data)
    logger.info(
        "tilda_payload_parsed request_id={} tenant_id={} endpoint={} source={} has_name={} has_phone={} has_email={} has_comment={} has_service={} extra_key_count={} unknown_key_count={} contains_unexpected_keys={} has_sensitive_known_keys={} sensitive_known_key_count={}",
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
        payload_shape["unknown_key_count"],
        payload_shape["contains_unexpected_keys"],
        payload_shape["has_sensitive_known_keys"],
        payload_shape["sensitive_known_key_count"],
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
    leads, total = await repo.get_list_scoped(
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
    lead = await repo.get_by_id_scoped(lead_id, tenant_id=tenant_id)
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
    lead = await repo.get_by_id_scoped(lead_id, tenant_id=tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    group_id = tenant.group_id if tenant else 0
    service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
    manager_tg_id = body.manager_tg_id or 0
    result = None
    transition_name = ""
    if body.status == LeadStatus.IN_PROGRESS:
        transition_name = "take"
        result = await service.take_in_progress(lead_id, manager_tg_id, None)
    elif body.status == LeadStatus.PAID:
        transition_name = "paid"
        result = await service.mark_paid(lead_id, manager_tg_id, body.amount, None)
    elif body.status == LeadStatus.SUCCESS:
        transition_name = "success"
        result = await service.mark_success(lead_id, manager_tg_id, None)
    elif body.status == LeadStatus.REJECTED:
        transition_name = "reject"
        result = await service.reject_lead(lead_id, manager_tg_id, body.reject_reason or "", None)
    if not result:
        return JSONResponse(status_code=409, content={"error": "invalid_transition"})

    result_lead_id = getattr(result, "id", None)
    if result_lead_id is None and isinstance(result, dict):
        result_lead_id = result.get("id")
    if result_lead_id is None:
        result_lead_id = lead_id

    await db.commit()
    try:
        logger.info(
            "lead_transition_post_commit_started lead_id={} tenant_id={} transition={} origin=api_patch",
            result_lead_id,
            tenant_id,
            transition_name,
        )
        await service.sync_lead_after_transition(result_lead_id, transition_name)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "lead_transition_post_commit_failed lead_id={} tenant_id={} transition={} origin=api_patch",
            result_lead_id,
            tenant_id,
            transition_name,
        )

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
    lead = await repo.get_by_id_scoped(lead_id, tenant_id=tenant_id)
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

