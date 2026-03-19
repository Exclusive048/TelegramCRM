from pydantic import BaseModel, Field
from typing import Any
from datetime import datetime
from app.db.models.lead import LeadStatus


class LeadCreateRequest(BaseModel):
    name:         str              = Field(..., min_length=1, max_length=255)
    phone:        str              = Field(..., min_length=5, max_length=50)
    email:        str | None       = None
    source:       str              = Field(..., description="tg_bot | website | landing | tilda | manual")
    comment:      str              = Field(..., min_length=1, description="Обязательный комментарий")
    service:      str | None       = None
    amount:       float | None     = None
    utm_campaign: str | None       = None
    utm_source:   str | None       = None
    manager_id:   int | None       = None
    extra:        dict[str, Any] | None = None


class TildaWebhookRequest(BaseModel):
    """Схема для документации, реальный парсинг через form-data"""
    Name:    str
    Phone:   str
    Comment: str | None = None


class LeadUpdateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    status:        LeadStatus = Field(..., description="Target status for transition")
    manager_tg_id: int | None = None  # FIXED #7
    reject_reason: str | None = None
    amount:        float | None = None


class LeadCommentRequest(BaseModel):
    text:   str = Field(..., min_length=1)
    author: str = "API"


class LeadResponse(BaseModel):
    id:           int
    name:         str
    phone:        str
    email:        str | None
    source:       str
    service:      str | None
    comment:      str
    amount:       float | None
    status:       LeadStatus
    manager_id:   int | None
    utm_campaign: str | None
    utm_source:   str | None
    extra:        dict | None
    created_at:   datetime
    updated_at:   datetime
    closed_at:    datetime | None
    model_config = {"from_attributes": True}


class LeadListResponse(BaseModel):
    total:    int
    page:     int
    per_page: int
    data:     list[LeadResponse]


class ArchiveLeadResponse(BaseModel):
    source_lead_id: int
    tenant_id: int | None
    lead_created_at: datetime
    lead_closed_at: datetime | None
    final_status: LeadStatus
    archived_at: datetime
    source: str
    service: str | None
    amount: float | None
    manager_id: int | None
    name: str
    phone: str
    email: str | None
    utm_campaign: str | None
    utm_source: str | None
    reject_reason: str | None
    tg_chat_id: int | None
    tg_topic_id: int | None
    tg_message_id: int | None
    model_config = {"from_attributes": True}


class ArchiveLeadListResponse(BaseModel):
    total: int
    page: int
    per_page: int
    data: list[ArchiveLeadResponse]


class ArchiveStatusAnalyticsResponse(BaseModel):
    total: int
    by_status: dict[str, int]


class CreateLeadResponse(BaseModel):
    status:        str = "ok"
    lead_id:       int
    tg_message_id: int | None = None


class OkResponse(BaseModel):
    status: str = "ok"
