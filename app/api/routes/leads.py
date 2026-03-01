from curses import raw

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.db.database import get_db
from app.db.repositories.lead_repository import LeadRepository
from app.db.models.lead import LeadStatus
from app.api.schemas.lead_schemas import (
    LeadCreateRequest, LeadUpdateRequest, LeadCommentRequest,
    LeadResponse, LeadListResponse, CreateLeadResponse, OkResponse,
    TildaWebhookRequest,
)
from app.api.deps import verify_api_key, get_current_sender
from app.services.lead_service import LeadService
from app.core.config import settings

router = APIRouter(prefix="/leads", tags=["Leads"])
MAX_EXTRA_KEYS = 20

# ── POST /leads ───────────────────────────────────────

@router.post("", response_model=CreateLeadResponse, status_code=201)
async def create_lead(
    body: LeadCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    repo = LeadRepository(db)
    service = LeadService(repo, sender)
    lead = await service.create_lead(body.model_dump(exclude_none=True))
    return CreateLeadResponse(lead_id=lead.id, tg_message_id=lead.tg_message_id)


# ── POST /leads/tilda — webhook для Tilda ─────────────

@router.post("/tilda", response_model=OkResponse, status_code=201, tags=["Integrations"])
async def tilda_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    """
    Webhook для Tilda.
    Tilda присылает form-data, мы нормализуем и создаём лид.
    """
    form = await request.form()
    data = dict(form)
    if settings.tilda_secret:
        if data.get('tilda_secret') != settings.tilda_secret:
            raise HTTPException(403, 'Invalid tilda secret')
    # Маппинг типичных полей Tilda → наши поля
    lead_data = {
        "name":    data.get("Name") or data.get("name") or data.get("NAME") or "—",
        "phone":   data.get("Phone") or data.get("phone") or data.get("PHONE") or "—",
        "source":  "tilda",
        "comment": data.get("Comment") or data.get("comment") or data.get("Message") or data.get("message") or "Заявка с Tilda",
        "service": data.get("Service") or data.get("service") or None,
        "utm_campaign": data.get("utm_campaign") or data.get("UTM_CAMPAIGN") or None,
        "utm_source":   data.get("utm_source") or None,
        "extra": {k: str(v)[:500] for k, v in list(raw.items())[:MAX_EXTRA_KEYS] if k not in 
                  {"Name", "Phone", "Comment", "Message", "Service", "utm_campaign", "utm_source", "formid", "formname", "tranid"}} or None,
    }

    repo = LeadRepository(db)
    service = LeadService(repo, sender)
    await service.create_lead({k: v for k, v in lead_data.items() if v is not None})
    return OkResponse()


# ── GET /leads ────────────────────────────────────────

@router.get("", response_model=LeadListResponse)
async def get_leads(
    status:     LeadStatus | None = Query(None),
    source:     str | None        = Query(None),
    manager_id: int | None        = Query(None),
    date_from:  datetime | None   = Query(None),
    date_to:    datetime | None   = Query(None),
    search:     str | None        = Query(None),
    page:       int               = Query(1, ge=1),
    per_page:   int               = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    repo = LeadRepository(db)
    leads, total = await repo.get_list(
        status=status, source=source, manager_id=manager_id,
        date_from=date_from, date_to=date_to, search=search,
        page=page, per_page=per_page,
    )
    return LeadListResponse(
        total=total, page=page, per_page=per_page,
        data=[LeadResponse.model_validate(l) for l in leads],
    )


# ── GET /leads/{id} ───────────────────────────────────

@router.get("/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return LeadResponse.model_validate(lead)


# ── PATCH /leads/{id} ─────────────────────────────────

@router.patch("/{lead_id}", response_model=OkResponse)
async def update_lead(
    lead_id: int,
    body: LeadUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if body.status:
        service = LeadService(repo, sender)
        if body.status == LeadStatus.IN_PROGRESS:
            await service.take_in_progress(lead_id, 0, None)
        elif body.status == LeadStatus.PAID:
            await service.mark_paid(lead_id, 0, body.amount, None)
        elif body.status == LeadStatus.SUCCESS:
            await service.mark_success(lead_id, 0, None)
        elif body.status == LeadStatus.REJECTED:
            await service.reject_lead(lead_id, 0, body.reject_reason or "", None)
    return OkResponse()


# ── POST /leads/{id}/comment ──────────────────────────

@router.post("/{lead_id}/comment", response_model=OkResponse, status_code=201)
async def add_comment(
    lead_id: int,
    body: LeadCommentRequest,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    service = LeadService(repo, sender)
    await service.add_comment(
        lead_id=lead_id,
        text=body.text,
        author=body.author,
        target_ref=None,
    )
    return OkResponse()
