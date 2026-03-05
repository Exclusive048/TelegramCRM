from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.lead import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0", index=True)
    owner_tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trial_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan: Mapped[str] = mapped_column(String(50), default="trial", nullable=False)
    # plan: trial | base | pro
    api_key: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    referral_code: Mapped[str | None] = mapped_column(
        String(16), unique=True, nullable=True, index=True
    )
    referred_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tenants.id"), nullable=True
    )
    referral_bonus_used: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # Onboarding
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # group_id is set on /setup; 0 means not bound yet

    # SLA settings (per-tenant)
    sla_new_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2"
    )
    sla_in_progress_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="3"
    )

    # Plan limits
    max_leads_per_month: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="50"
    )  # 50 for trial, -1 = unlimited
    max_managers: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="3"
    )  # 3 for trial, -1 = unlimited

    # Monthly lead counter (reset by scheduler)
    leads_this_month: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    leads_month_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Expiry notification (avoid spamming)
    expiry_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False)
    yukassa_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    period_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    # status: pending | succeeded | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")
