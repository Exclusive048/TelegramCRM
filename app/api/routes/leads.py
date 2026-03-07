from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import limiter
from app.api.deps import get_current_sender, verify_api_key
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
    (messenger-id, program, city и т.д.).
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

    # ── Телефон / контакт ──────────────────────────────────────────────────────
    phone = _pick(data, "Phone", "phone", "PHONE", "tel", "Tel", "telephone")
    if not phone:
        messenger_id = _pick(data, "messenger-id", "messenger_id")
        messenger_type = _pick(data, "messenger-type", "messenger_type")
        if messenger_id:
            prefix = f"{messenger_type}: " if messenger_type else ""
            phone = f"{prefix}{messenger_id}"
    if not phone:
        phone = _pick(data, "email", "Email", "EMAIL", default="—")

    # ── Email ─────────────────────────────────────────────────────────────────
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
        ("city", "Город"),
        ("City", "Город"),
        ("country", "Страна"),
        ("location", "Локация"),
        ("time", "Время"),
    ]:
        val = data.get(field)
        if val and str(val).strip():
            comment_parts.append(f"{label}: {val}")

    if not comment_parts:
        comment_parts.append("Заявка с Tilda")

    comment = " | ".join(comment_parts)

    # ── UTM ──────────────────────────────────────────────────────────────────
    utm_campaign = _pick(data, "utm_campaign", "UTM_CAMPAIGN") or None
    utm_source = _pick(data, "utm_source", "utm_medium") or None

    # ── Extra — всё остальное полезное ────────────────────────────────────────
    extra = {}
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


# ── POST /leads ───────────────────────────────────────────────────────────────

@router.post("", response_model=CreateLeadResponse, status_code=201)
@limiter.limit("60/minute")
async def create_lead(
    body: LeadCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    tenant = request.state.tenant
    tenant_id = tenant.id if tenant else None
    if tenant and not tenant.group_id:
        return JSONResponse(
            status_code=409,
            content={"error": "group_not_configured"},
        )
    if tenant and tenant.max_leads_per_month != -1:
        async with AsyncSessionLocal() as check_session:
            check_repo = TenantRepository(check_session)
            new_count = await check_repo.increment_leads_count(tenant.id)
            await check_session.commit()
        if new_count > tenant.max_leads_per_month:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Достигнут лимит {tenant.max_leads_per_month} заявок в месяц. "
                    "Перейдите на тариф Базовый для снятия ограничений."
                ),
            )
    repo = LeadRepository(db)
    group_id = tenant.group_id if tenant else 0
    service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
    lead = await service.create_lead(body.model_dump(exclude_none=True))
    return CreateLeadResponse(lead_id=lead.id, tg_message_id=lead.tg_message_id)


# ── POST /leads/tilda — webhook для Tilda ─────────────────────────────────────

@router.post("/tilda", response_model=OkResponse, status_code=201, tags=["Integrations"])
@limiter.limit("60/minute")
async def tilda_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    """
    Webhook для Tilda.
    Принимает form-data или JSON, парсит гибко под любую форму.
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        # form-data (стандартный формат Tilda)
        form = await request.form()
        data = dict(form)

    logger.debug(f"tilda_webhook raw fields: {list(data.keys())}")

    lead_data = _parse_tilda(data)
    logger.info(f"tilda_webhook parsed: name={lead_data['name']!r} phone={lead_data['phone']!r} service={lead_data['service']!r}")

    tenant = request.state.tenant
    if tenant and tenant.max_leads_per_month != -1:
        async with AsyncSessionLocal() as check_session:
            check_repo = TenantRepository(check_session)
            new_count = await check_repo.increment_leads_count(tenant.id)
            await check_session.commit()
        if new_count > tenant.max_leads_per_month:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Достигнут лимит {tenant.max_leads_per_month} заявок в месяц. "
                    "Перейдите на тариф Базовый для снятия ограничений."
                ),
            )
    repo = LeadRepository(db)
    group_id = tenant.group_id if tenant else 0
    tenant_id = tenant.id if tenant else None
    service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
    await service.create_lead({k: v for k, v in lead_data.items() if v is not None})
    return OkResponse()


# ── GET /leads ────────────────────────────────────────────────────────────────

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
    _: None = Depends(verify_api_key),
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


# ── GET /leads/{id} ───────────────────────────────────────────────────────────

@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    tenant = request.state.tenant
    tenant_id = tenant.id if tenant else None
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id, tenant_id=tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return LeadResponse.model_validate(lead)


# ── PATCH /leads/{id} ────────────────────────────────────────────────────────

@router.patch("/{lead_id}", response_model=OkResponse)
async def update_lead(
    lead_id: int,
    body: LeadUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    tenant = request.state.tenant
    tenant_id = tenant.id if tenant else None
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id, tenant_id=tenant_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if body.status:
        group_id = tenant.group_id if tenant else 0
        service = LeadService(repo, sender, group_id=group_id, tenant_id=tenant_id)
        manager_tg_id = body.manager_tg_id
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


# ── POST /leads/{id}/comment ──────────────────────────────────────────────────

@router.post("/{lead_id}/comment", response_model=OkResponse, status_code=201)
async def add_comment(
    lead_id: int,
    body: LeadCommentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
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
