from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.db.database import get_db, AsyncSessionLocal
from app.db.repositories.lead_repository import LeadRepository
from app.db.repositories.tenant_repository import TenantRepository
from app.db.models.lead import LeadStatus
from app.api.schemas.lead_schemas import (
    LeadCreateRequest, LeadUpdateRequest, LeadCommentRequest,
    LeadResponse, LeadListResponse, CreateLeadResponse, OkResponse,
    TildaWebhookRequest,
)
from app.api.deps import verify_api_key, get_current_sender
from app.services.lead_service import LeadService

router = APIRouter(prefix="/leads", tags=["Leads"])
MAX_EXTRA_KEYS = 20

# в”Ђв”Ђ POST /leads в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”ЂvЂvЂvЂvЂv

@router.post("", response_model=CreateLeadResponse, status_code=201)
async def create_lead(
    body: LeadCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    # Проверить лимит лидов за месяц
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
                )
            )
    repo = LeadRepository(db)
    group_id = tenant.group_id if tenant else 0
    service = LeadService(repo, sender, group_id=group_id)
    lead = await service.create_lead(body.model_dump(exclude_none=True))
    return CreateLeadResponse(lead_id=lead.id, tg_message_id=lead.tg_message_id)


# в”Ђв”Ђ POST /leads/tilda вЂ” webhook РґР»СЏ Tilda в”Ђв”Ђв”Ђв”ЂvЂv

@router.post("/tilda", response_model=OkResponse, status_code=201, tags=["Integrations"])
async def tilda_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    """
    Webhook РґР»СЏ Tilda.
    Tilda РїСЂРёСЃС‹Р»Р°РµС‚ form-data, РјС‹ РЅРѕСЂРјР°Р»РёР·СѓРµРј Рё СЃРѕР·РґР°С‘Рј Р»РёРґ.
    """
    form = await request.form()
    data = dict(form)
    # РњР°РїРїРёРЅРі С‚РёРїРёС‡РЅС‹С… РїРѕР»РµР№ Tilda в†’ РЅР°С€Рё РїРѕР»СЏ
    lead_data = {
        "name":    data.get("Name") or data.get("name") or data.get("NAME") or "вЂ”",
        "phone":   data.get("Phone") or data.get("phone") or data.get("PHONE") or "вЂ”",
        "source":  "tilda",
        "comment": data.get("Comment") or data.get("comment") or data.get("Message") or data.get("message") or "Р—Р°СЏРІРєР° СЃ Tilda",
        "service": data.get("Service") or data.get("service") or None,
        "utm_campaign": data.get("utm_campaign") or data.get("UTM_CAMPAIGN") or None,
        "utm_source":   data.get("utm_source") or None,
        "extra": {k: str(v)[:500] for k, v in list(data.items())[:MAX_EXTRA_KEYS] if k not in 
                  {"Name", "Phone", "Comment", "Message", "Service", "utm_campaign", "utm_source", "formid", "formname", "tranid"}} or None,
    }

    tenant = request.state.tenant
    repo = LeadRepository(db)
    group_id = tenant.group_id if tenant else 0
    service = LeadService(repo, sender, group_id=group_id)
    await service.create_lead({k: v for k, v in lead_data.items() if v is not None})
    return OkResponse()


# в”Ђв”Ђ GET /leads в”Ђв”Ђв”ЂvЂvЂvЂvЂvЂvЂvЂvЂvЂvЂvЂvЂv

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


# в”Ђв”Ђ GET /leads/{id} в”ЂvЂv

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


# в”Ђв”Ђ PATCH /leads/{id} в”ЂvЂv

@router.patch("/{lead_id}", response_model=OkResponse)
async def update_lead(
    lead_id: int,
    body: LeadUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if body.status:
        tenant = request.state.tenant
        group_id = tenant.group_id if tenant else 0
        service = LeadService(repo, sender, group_id=group_id)
        manager_tg_id = body.manager_tg_id  # FIXED #7
        if body.status == LeadStatus.IN_PROGRESS:
            await service.take_in_progress(lead_id, manager_tg_id, None)  # FIXED #7
        elif body.status == LeadStatus.PAID:
            await service.mark_paid(lead_id, manager_tg_id, body.amount, None)  # FIXED #7
        elif body.status == LeadStatus.SUCCESS:
            await service.mark_success(lead_id, manager_tg_id, None)  # FIXED #7
        elif body.status == LeadStatus.REJECTED:
            await service.reject_lead(lead_id, manager_tg_id, body.reject_reason or "", None)  # FIXED #7
    return OkResponse()


# в”ЂvЂv POST /leads/{id}/comment vЂv

@router.post("/{lead_id}/comment", response_model=OkResponse, status_code=201)
async def add_comment(
    lead_id: int,
    body: LeadCommentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_api_key),
    sender=Depends(get_current_sender),
):
    repo = LeadRepository(db)
    lead = await repo.get_by_id(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    tenant = request.state.tenant
    group_id = tenant.group_id if tenant else 0
    service = LeadService(repo, sender, group_id=group_id)
    await service.add_comment(
        lead_id=lead_id,
        text=body.text,
        author=body.author,
        target_ref=None,
    )
    return OkResponse()
