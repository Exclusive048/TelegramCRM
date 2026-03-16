from datetime import datetime, timedelta, timezone
import secrets
import string

from sqlalchemy import case, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.lead import Manager
from app.db.models.tenant import Tenant, Payment
from app.db.utils import _naive


def _generate_referral_code() -> str:
    """Р вЂњР ВµР Р…Р ВµРЎР‚Р С‘РЎР‚РЎС“Р ВµРЎвЂљ Р С”Р С•РЎР‚Р С•РЎвЂљР С”Р С‘Р в„– РЎвЂЎР С‘РЎвЂљР В°Р ВµР СРЎвЂ№Р в„– РЎР‚Р ВµРЎвЂћР ВµРЎР‚Р В°Р В»РЎРЉР Р…РЎвЂ№Р в„– Р С”Р С•Р Т‘: 8 РЎРѓР С‘Р СР Р†Р С•Р В»Р С•Р Р† A-Z0-9."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


class TenantRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_group_id(self, group_id: int) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(Tenant.group_id == group_id)
        )
        return result.scalar_one_or_none()

    async def bind_group(self, tenant_id: int, group_id: int) -> None:
        """Р СџРЎР‚Р С‘Р Р†РЎРЏР В·Р В°РЎвЂљРЎРЉ Р С–РЎР‚РЎС“Р С—Р С—РЎС“ Р С” РЎвЂљР ВµР Р…Р В°Р Р…РЎвЂљРЎС“. Р вЂ™РЎвЂ№Р В·РЎвЂ№Р Р†Р В°Р ВµРЎвЂљРЎРѓРЎРЏ Р С—РЎР‚Р С‘ /setup."""
        await self.session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(
                group_id=group_id
            )
        )

    async def complete_onboarding(self, tenant_id: int) -> None:
        """Р С›РЎвЂљР СР ВµРЎвЂљР С‘РЎвЂљРЎРЉ РЎвЂЎРЎвЂљР С• /setup Р Р†РЎвЂ№Р С—Р С•Р В»Р Р…Р ВµР Р… РЎС“РЎРѓР С—Р ВµРЎв‚¬Р Р…Р С•."""
        await self.session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(
                onboarding_completed=True
            )
        )

    async def set_tenant_limits(
        self,
        tenant_id: int,
        max_leads: int,
        max_managers: int,
        sla_new_hours: int | None = None,
        sla_in_progress_days: int | None = None,
    ) -> None:
        """Р Р€РЎРѓРЎвЂљР В°Р Р…Р С•Р Р†Р С‘РЎвЂљРЎРЉ Р В»Р С‘Р СР С‘РЎвЂљРЎвЂ№ Р С—РЎР‚Р С‘ РЎРѓР СР ВµР Р…Р Вµ РЎвЂљР В°РЎР‚Р С‘РЎвЂћР В°."""
        values = {
            "max_leads_per_month": max_leads,
            "max_managers": max_managers,
        }
        if sla_new_hours is not None:
            values["sla_new_hours"] = sla_new_hours
        if sla_in_progress_days is not None:
            values["sla_in_progress_days"] = sla_in_progress_days
        await self.session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(**values)
        )

    @staticmethod
    def _monthly_counter_expressions(now_naive: datetime):
        month_start = datetime(now_naive.year, now_naive.month, 1)
        needs_reset = or_(
            Tenant.leads_month_reset_at.is_(None),
            Tenant.leads_month_reset_at < month_start,
        )
        next_count = case(
            (needs_reset, 1),
            else_=func.coalesce(Tenant.leads_this_month, 0) + 1,
        )
        next_reset_at = case(
            (needs_reset, now_naive),
            else_=Tenant.leads_month_reset_at,
        )
        return next_count, next_reset_at

    async def increment_leads_count(self, tenant_id: int) -> int:
        """Atomically increments monthly lead counter and returns new value."""
        now_naive = _naive(datetime.now(timezone.utc))
        if now_naive is None:
            return 0

        next_count, next_reset_at = self._monthly_counter_expressions(now_naive)
        result = await self.session.execute(
            update(Tenant)
            .where(Tenant.id == tenant_id)
            .values(
                leads_this_month=next_count,
                leads_month_reset_at=next_reset_at,
            )
            .returning(Tenant.leads_this_month)
        )
        new_count = result.scalar_one_or_none()
        return int(new_count) if new_count is not None else 0

    async def try_reserve_monthly_lead_quota(self, tenant_id: int) -> tuple[bool, int, int]:
        """
        Atomically checks quota and reserves one lead slot for current month.
        Returns: (allowed, new_or_current_count, max_limit).
        """
        now_naive = _naive(datetime.now(timezone.utc))
        if now_naive is None:
            raise ValueError("Failed to build current timestamp")

        next_count, next_reset_at = self._monthly_counter_expressions(now_naive)
        result = await self.session.execute(
            update(Tenant)
            .where(
                Tenant.id == tenant_id,
                or_(
                    Tenant.max_leads_per_month == -1,
                    next_count <= Tenant.max_leads_per_month,
                ),
            )
            .values(
                leads_this_month=next_count,
                leads_month_reset_at=next_reset_at,
            )
            .returning(Tenant.leads_this_month, Tenant.max_leads_per_month)
        )
        row = result.first()
        if row is not None:
            return True, int(row[0]), int(row[1])

        tenant = await self.get_by_id(tenant_id)
        if tenant is None:
            raise ValueError(f"Tenant not found: {tenant_id}")
        return False, int(tenant.leads_this_month or 0), int(tenant.max_leads_per_month)

    async def get_by_owner(self, owner_tg_id: int) -> list[Tenant]:
        result = await self.session.execute(
            select(Tenant)
            .where(Tenant.owner_tg_id == owner_tg_id)
            .order_by(Tenant.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_referral_code(self, code: str) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(Tenant.referral_code == code.upper())
        )
        return result.scalar_one_or_none()

    async def get_by_api_key(self, api_key: str) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(
                Tenant.api_key == api_key,
                Tenant.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_management_api_key(self, api_key: str) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(
                Tenant.management_api_key == api_key,
                Tenant.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_api_key_any(self, api_key: str) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(
                or_(
                    Tenant.api_key == api_key,
                    Tenant.management_api_key == api_key,
                )
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, tenant_id: int) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def get_tenant_ids_without_management_api_key(self, *, limit: int | None = None) -> list[int]:
        stmt = (
            select(Tenant.id)
            .where(Tenant.management_api_key.is_(None))
            .order_by(Tenant.id.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_without_management_api_key(self) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Tenant)
            .where(Tenant.management_api_key.is_(None))
        )
        return int(result.scalar_one())

    async def create(
        self,
        owner_tg_id: int,
        company_name: str,
        referred_by_id: int | None = None,
        *,
        group_id: int = 0,
        generate_api_key: bool = True,
    ) -> Tenant:
        while True:
            code = _generate_referral_code()
            existing = await self.get_by_referral_code(code)
            if not existing:
                break

        tenant = Tenant(
            group_id=group_id,
            owner_tg_id=owner_tg_id,
            company_name=company_name,
            is_active=False,
            referral_code=code,
            referred_by_id=referred_by_id,
            sla_new_hours=settings.sla_new_hours,
            sla_in_progress_days=settings.sla_in_progress_days,
        )
        self.session.add(tenant)
        await self.session.flush()
        if generate_api_key:
            tenant.api_key = await self._ensure_api_key(tenant.id)
            tenant.management_api_key = await self._ensure_management_api_key(tenant.id)
        return tenant

    async def create_tenant(
        self,
        owner_tg_id: int,
        company_name: str,
        referred_by_id: int | None = None,
        *,
        group_id: int = 0,
    ) -> Tenant:
        return await self.create(
            owner_tg_id=owner_tg_id,
            company_name=company_name,
            referred_by_id=referred_by_id,
            group_id=group_id,
            generate_api_key=True,
        )

    async def get_tenants_by_owner(self, owner_tg_id: int) -> list[Tenant]:
        return await self.get_by_owner(owner_tg_id)

    async def _ensure_api_key(self, tenant_id: int) -> str:
        """Р вЂњР ВµР Р…Р ВµРЎР‚Р С‘РЎР‚РЎС“Р ВµРЎвЂљ API Р С”Р В»РЎР‹РЎвЂЎ Р ВµРЎРѓР В»Р С‘ Р ВµР С–Р С• Р ВµРЎвЂ°РЎвЂ Р Р…Р ВµРЎвЂљ. Р вЂ™Р С•Р В·Р Р†РЎР‚Р В°РЎвЂ°Р В°Р ВµРЎвЂљ Р С”Р В»РЎР‹РЎвЂЎ."""
        tenant = await self.get_by_id(tenant_id)
        if tenant.api_key:
            return tenant.api_key
        while True:
            key = secrets.token_urlsafe(32)
            existing = await self.get_by_api_key_any(key)
            if not existing:
                break
        await self.session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(api_key=key)
        )
        await self.session.flush()
        return key

    async def _ensure_management_api_key(self, tenant_id: int) -> str:
        """Generate a management API key if missing and return it."""
        tenant = await self.get_by_id(tenant_id)
        if tenant.management_api_key:
            return tenant.management_api_key
        while True:
            key = secrets.token_urlsafe(32)
            existing = await self.get_by_api_key_any(key)
            if not existing:
                break
        await self.session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(management_api_key=key)
        )
        await self.session.flush()
        return key

    async def ensure_management_api_key(self, tenant_id: int) -> str:
        return await self._ensure_management_api_key(tenant_id)

    async def activate_trial(self, tenant_id: int, days: int = 14) -> str:
        """Activates a trial period and returns API key."""
        tenant = await self.get_by_id(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant not found: {tenant_id}")
        until = datetime.now(timezone.utc) + timedelta(days=days)
        await self.session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(
                is_active=True,
                trial_used=True,
                trial_until=_naive(until),
                subscription_until=_naive(until),
                plan="trial",
            )
        )
        await self.session.execute(
            update(Manager)
            .where(Manager.tg_id == tenant.owner_tg_id)
            .values(owner_trial_used=True)
        )
        from app.core.plans import get_plan_limits
        limits = get_plan_limits("trial")
        await self.set_tenant_limits(
            tenant_id,
            max_leads=limits["max_leads_per_month"],
            max_managers=limits["max_managers"],
            sla_new_hours=limits["sla_new_hours"],
            sla_in_progress_days=limits["sla_in_progress_days"],
        )
        api_key = await self._ensure_api_key(tenant_id)
        await self._ensure_management_api_key(tenant_id)
        return api_key

    async def has_owner_used_trial(self, owner_tg_id: int) -> bool:
        manager_used = await self.session.scalar(
            select(
                exists().where(
                    Manager.tg_id == owner_tg_id,
                    Manager.owner_trial_used == True,
                )
            )
        )
        if bool(manager_used):
            return True

        tenant_used = await self.session.scalar(
            select(
                exists().where(
                    Tenant.owner_tg_id == owner_tg_id,
                    Tenant.trial_used == True,
                )
            )
        )
        return bool(tenant_used)

    async def activate_subscription(self, tenant_id: int, days: int = 30) -> tuple[datetime, str]:
        """Р СџРЎР‚Р С•Р Т‘Р В»Р ВµР Р†Р В°Р ВµРЎвЂљ Р С—Р С•Р Т‘Р С—Р С‘РЎРѓР С”РЎС“. Р вЂ™Р С•Р В·Р Р†РЎР‚Р В°РЎвЂ°Р В°Р ВµРЎвЂљ (Р Р…Р С•Р Р†Р В°РЎРЏ_Р Т‘Р В°РЎвЂљР В°, api_key)."""
        tenant = await self.get_by_id(tenant_id)
        now = datetime.now(timezone.utc)
        base = max(tenant.subscription_until or now, now)
        new_until = base + timedelta(days=days)
        await self.session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(
                is_active=True,
                subscription_until=_naive(new_until),
                plan="base",
            )
        )
        api_key = await self._ensure_api_key(tenant_id)
        await self._ensure_management_api_key(tenant_id)

        from app.core.plans import get_plan_limits
        limits = get_plan_limits("base")
        await self.set_tenant_limits(
            tenant_id,
            max_leads=limits["max_leads_per_month"],
            max_managers=limits["max_managers"],
            sla_new_hours=limits["sla_new_hours"],
            sla_in_progress_days=limits["sla_in_progress_days"],
        )

        if tenant.referred_by_id and not tenant.referral_bonus_used:
            bonus_days = settings.referral_bonus_days
            referrer = await self.get_by_id(tenant.referred_by_id)
            if referrer and referrer.is_active:
                referrer_base = max(referrer.subscription_until or now, now)
                await self.session.execute(
                    update(Tenant).where(Tenant.id == referrer.id).values(
                        subscription_until=_naive(referrer_base + timedelta(days=bonus_days))
                    )
                )
                await self.session.execute(
                    update(Tenant).where(Tenant.id == tenant_id).values(
                        referral_bonus_used=True
                    )
                )

        return new_until, api_key

    async def deactivate(self, tenant_id: int) -> None:
        await self.session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(is_active=False)
        )

    async def get_all(self) -> list[Tenant]:
        result = await self.session.execute(
            select(Tenant).order_by(Tenant.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_expiring_soon(self, days: int = 3) -> list[Tenant]:
        now = datetime.now(timezone.utc)
        until = now + timedelta(days=days)
        result = await self.session.execute(
            select(Tenant).where(
                Tenant.is_active == True,
                Tenant.subscription_until != None,
                Tenant.subscription_until <= until,
                Tenant.subscription_until > now,
            )
        )
        return list(result.scalars().all())

    async def get_referral_stats(self, tenant_id: int) -> dict:
        """Р РЋРЎвЂљР В°РЎвЂљР С‘РЎРѓРЎвЂљР С‘Р С”Р В° РЎР‚Р ВµРЎвЂћР ВµРЎР‚Р В°Р В»Р С•Р Р† Р Т‘Р В»РЎРЏ Р Т‘Р В°Р Р…Р Р…Р С•Р С–Р С• РЎвЂљР ВµР Р…Р В°Р Р…РЎвЂљР В°."""
        result = await self.session.execute(
            select(Tenant).where(Tenant.referred_by_id == tenant_id)
        )
        referrals = list(result.scalars().all())
        paid = [r for r in referrals if r.plan != "trial" and r.is_active]
        return {
            "total": len(referrals),
            "paid": len(paid),
            "bonus_days_earned": len(paid) * settings.referral_bonus_days,
        }

    async def create_payment(
        self,
        tenant_id: int,
        amount: float,
        yukassa_id: str | None = None,
        period_days: int = 30,
    ) -> Payment:
        payment = Payment(
            tenant_id=tenant_id,
            yukassa_id=yukassa_id,
            amount=amount,
            period_days=period_days,
            status="pending",
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def get_payment_by_yukassa_id(self, yukassa_id: str) -> Payment | None:
        result = await self.session.execute(
            select(Payment).where(Payment.yukassa_id == yukassa_id)
        )
        return result.scalar_one_or_none()

    async def mark_payment_succeeded(self, yukassa_id: str) -> Payment | None:
        updated_id = (
            await self.session.execute(
                update(Payment)
                .where(Payment.yukassa_id == yukassa_id, Payment.status == "pending")
                .values(status="succeeded")
                .returning(Payment.id)
            )
        ).scalar_one_or_none()
        if updated_id is None:
            return None
        result = await self.session.execute(
            select(Payment).where(Payment.id == updated_id)
        )
        return result.scalar_one_or_none()

