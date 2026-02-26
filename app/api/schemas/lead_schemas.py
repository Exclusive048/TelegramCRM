from pydantic import BaseModel, Field
from typing import Any
from datetime import datetime
from app.db.models.lead import LeadStatus


class LeadCreateRequest(BaseModel):
    name:         str              = Field(..., min_length=1, max_length=255)
    phone:        str              = Field(..., min_length=5, max_length=50)
    source:       str              = Field(..., description="tg_bot | website | landing | tilda | manual")
    comment:      str              = Field(..., min_length=1, description="Обязательный комментарий")
    service:      str | None       = None
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
    status:        LeadStatus | None = None
    manager_id:    int | None        = None
    reject_reason: str | None        = None
    service:       str | None        = None
    extra:         dict[str, Any] | None = None


class LeadCommentRequest(BaseModel):
    text:   str = Field(..., min_length=1)
    author: str = "API"


class LeadResponse(BaseModel):
    id:           int
    name:         str
    phone:        str
    source:       str
    service:      str | None
    comment:      str
    status:       LeadStatus
    manager_id:   int | None
    utm_campaign: str | None
    utm_source:   str | None
    extra:        dict | None
    created_at:   datetime
    updated_at:   datetime
    model_config = {"from_attributes": True}


class LeadListResponse(BaseModel):
    total:    int
    page:     int
    per_page: int
    data:     list[LeadResponse]


class CreateLeadResponse(BaseModel):
    status:        str = "ok"
    lead_id:       int
    tg_message_id: int | None = None


class OkResponse(BaseModel):
    status: str = "ok"
