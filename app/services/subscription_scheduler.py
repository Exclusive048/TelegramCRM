"""
Фоновые задачи по подпискам:
- Уведомление за 3 дня до истечения
- Авто-деактивация при истечении
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from app.db.database import AsyncSessionLocal
from sqlalchemy import select, update
from app.db.models.tenant import Tenant


async def _check_subscriptions():
    """
    Запускается каждый час.
    1. Деактивирует истёкшие подписки
    2. Отправляет напоминание за 3 дня до истечения (один раз)
    """
    from master_bot.notify import notify_tenant_owner, notify_admin
    now = datetime.now(timezone.utc)
    warn_threshold = now + timedelta(days=3)

    async with AsyncSessionLocal() as session:
        # Все активные тенанты с подпиской
        result = await session.execute(
            select(Tenant).where(
                Tenant.is_active == True,
                Tenant.subscription_until != None,
            )
        )
        tenants = list(result.scalars().all())

    for tenant in tenants:
        sub_until = tenant.subscription_until
        if not sub_until:
            continue

        # 1. Деактивировать истёкшие
        if sub_until < now:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Tenant)
                    .where(Tenant.id == tenant.id)
                    .values(is_active=False)
                )
                await session.commit()
            logger.info(f"subscription_expired tenant_id={tenant.id}")
            await notify_admin(
                f"🔴 Подписка истекла — автодеактивация\n"
                f"🏢 <b>{tenant.company_name}</b> (ID:{tenant.id})"
            )
            await notify_tenant_owner(
                tenant.owner_tg_id,
                f"⏰ <b>Подписка истекла</b>\n\n"
                f"Доступ к CRM приостановлен.\n"
                f"Напишите /start для продления подписки."
            )
            continue

        # 2. Предупредить за 3 дня (один раз)
        if sub_until <= warn_threshold:
            already_notified = (
                tenant.expiry_notified_at is not None
                and (now - tenant.expiry_notified_at).days < 1
            )
            if not already_notified:
                days_left = (sub_until - now).days
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(Tenant)
                        .where(Tenant.id == tenant.id)
                        .values(expiry_notified_at=now)
                    )
                    await session.commit()
                await notify_tenant_owner(
                    tenant.owner_tg_id,
                    f"⚠️ <b>Подписка истекает через {days_left} дн.</b>\n\n"
                    f"🏢 {tenant.company_name}\n"
                    f"📅 Дата: {sub_until.strftime('%d.%m.%Y')}\n\n"
                    f"Продлите подписку чтобы не потерять доступ: /start"
                )
                logger.info(
                    f"expiry_warning_sent tenant_id={tenant.id} days_left={days_left}"
                )


_sub_scheduler: AsyncIOScheduler | None = None


def start_subscription_scheduler():
    global _sub_scheduler
    if _sub_scheduler is not None:
        return
    _sub_scheduler = AsyncIOScheduler(timezone="UTC")
    _sub_scheduler.add_job(
        _check_subscriptions,
        trigger=IntervalTrigger(hours=1),
        id="check_subscriptions",
        replace_existing=True,
        misfire_grace_time=300,
    )
    _sub_scheduler.start()
    logger.info("subscription_scheduler started")
