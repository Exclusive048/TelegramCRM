from datetime import datetime, timedelta, timezone
import secrets
import string

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.tenant import Tenant, Payment
from app.db.utils import _naive


def _generate_referral_code() -> str:
    """Генерирует короткий читаемый реферальный код: 8 символов A-Z0-9."""
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
        """Привязать группу к тенанту. Вызывается при /setup."""
        await self.session.execute(
            update(Tenant).where(Tenant.id == tenant_id).values(
                group_id=group_id
            )
        )

    async def complete_onboarding(self, tenant_id: int) -> None:
        """Отметить что /setup выполнен успешно."""
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
        """Установить лимиты при смене тарифа."""
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

    async def increment_leads_count(self, tenant_id: int) -> int:
        """
        Увеличить счётчик лидов за месяц.
        Автоматически сбрасывает счётчик если наступил новый месяц.
        Возвращает новое значение счётчика.
        """
        from datetime import datetime, timezone
        tenant = await self.get_by_id(tenant_id)
        if not tenant:
            return 0
        now = datetime.now(timezone.utc)

        # Сброс счётчика если наступил новый месяц
        if (tenant.leads_month_reset_at is None or
                tenant.leads_month_reset_at.month != now.month or
                tenant.leads_month_reset_at.year != now.year):
            new_count = 1
            await self.session.execute(
                update(Tenant).where(Tenant.id == tenant_id).values(
                    leads_this_month=1,
                    leads_month_reset_at=_naive(now),
                )
            )
        else:
            new_count = (tenant.leads_this_month or 0) + 1
            await self.session.execute(
                update(Tenant).where(Tenant.id == tenant_id).values(
                    leads_this_month=new_count,
                )
            )
        return new_count

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

    async def get_by_api_key_any(self, api_key: str) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(Tenant.api_key == api_key)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, tenant_id: int) -> Tenant | None:
        result = await self.session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        return result.scalar_one_or_none()

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
        """Генерирует API ключ если его ещё нет. Возвращает ключ."""
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

    async def activate_trial(self, tenant_id: int, days: int = 14) -> str:
        """Активирует пробный период. Возвращает api_key."""
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
        from app.core.plans import get_plan_limits
        limits = get_plan_limits("trial")
        await self.set_tenant_limits(
            tenant_id,
            max_leads=limits["max_leads_per_month"],
            max_managers=limits["max_managers"],
            sla_new_hours=limits["sla_new_hours"],
            sla_in_progress_days=limits["sla_in_progress_days"],
        )
        return await self._ensure_api_key(tenant_id)

    async def activate_subscription(self, tenant_id: int, days: int = 30) -> tuple[datetime, str]:
        """Продлевает подписку. Возвращает (новая_дата, api_key)."""
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
        """Статистика рефералов для данного тенанта."""
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
