import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Index
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# ── Enums ───────────────────────────────────────────────────────

class LeadStatus(str, enum.Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    PAID = "paid"
    SUCCESS = "success"
    REJECTED = "rejected"


class ManagerRole(str, enum.Enum):
    MANAGER = "manager"  # обрабатывает заявки
    ADMIN = "admin"      # + назначает менеджеров, видит всю статистику, делает выгрузки


def pg_enum(enum_cls: type[enum.Enum], *, name: str) -> SAEnum:
    """
    Postgres ENUM mapping that ALWAYS persists enum.value (not enum.name),
    validates strings, and uses an existing PG type with fixed name.
    """
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        validate_strings=True,
        values_callable=lambda cls: [e.value for e in cls],
    )


# Reuse the same ENUM objects across all columns to avoid inconsistencies.
LeadStatusEnum = pg_enum(LeadStatus, name="leadstatus")
ManagerRoleEnum = pg_enum(ManagerRole, name="managerrole")


# ── Models ──────────────────────────────────────────────────────

class Manager(Base):
    __tablename__ = "managers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    tg_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tenant_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tenants.id"), nullable=True, index=True
    )

    role: Mapped[ManagerRole] = mapped_column(
        ManagerRoleEnum,
        nullable=False,
        default=ManagerRole.MANAGER,
        server_default=ManagerRole.MANAGER.value,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    leads: Mapped[list["Lead"]] = relationship("Lead", back_populates="manager")

    @property
    def is_admin(self) -> bool:
        return self.role == ManagerRole.ADMIN


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tenants.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(100))
    service: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[LeadStatus] = mapped_column(
        LeadStatusEnum,
        nullable=False,
        default=LeadStatus.NEW,
        server_default=LeadStatus.NEW.value,
    )
    __table_args__ = (
        Index('ix_leads_status', 'status'),
        Index('ix_leads_created_at', 'created_at'),
    )
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("managers.id"), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    tg_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tg_topic_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    manager: Mapped["Manager | None"] = relationship("Manager", back_populates="leads")
    history: Mapped[list["LeadHistory"]] = relationship(
        "LeadHistory",
        back_populates="lead",
        order_by="LeadHistory.created_at",
    )
    comments: Mapped[list["LeadComment"]] = relationship(
        "LeadComment",
        back_populates="lead",
        order_by="LeadComment.created_at",
    )
    card_messages: Mapped[list["LeadCardMessage"]] = relationship(
        "LeadCardMessage",
        back_populates="lead",
        order_by="LeadCardMessage.created_at",
    )
    reminders: Mapped[list["Reminder"]] = relationship(
        "Reminder",
        back_populates="lead",
        order_by="Reminder.created_at",
    )


class LeadCardMessage(Base):
    __tablename__ = "lead_card_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    lead: Mapped["Lead"] = relationship("Lead", back_populates="card_messages")


class PanelMessage(Base):
    __tablename__ = "panel_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "topic_id", name="uq_panel_messages_chat_topic"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TenantTopic(Base):
    __tablename__ = "tenant_topics"
    __table_args__ = (
        UniqueConstraint("chat_id", "key", name="uq_tenant_topics_chat_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LeadHistory(Base):
    __tablename__ = "lead_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))

    from_status: Mapped[LeadStatus | None] = mapped_column(
        LeadStatusEnum,
        nullable=True,
    )
    to_status: Mapped[LeadStatus] = mapped_column(
        LeadStatusEnum,
        nullable=False,
    )

    manager_id: Mapped[int | None] = mapped_column(ForeignKey("managers.id"), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    lead: Mapped["Lead"] = relationship("Lead", back_populates="history")


class LeadComment(Base):
    __tablename__ = "lead_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    lead: Mapped["Lead"] = relationship("Lead", back_populates="comments")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id"))
    manager_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_processing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lead: Mapped["Lead"] = relationship("Lead", back_populates="reminders")
