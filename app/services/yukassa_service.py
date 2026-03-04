import uuid

import httpx
from loguru import logger

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.repositories.tenant_repository import TenantRepository


async def create_yukassa_payment(tenant_id: int, company_name: str) -> str | None:
    """Создаёт платёж в ЮКасса, сохраняет в БД, возвращает ссылку."""
    if not settings.yukassa_shop_id or not settings.yukassa_secret_key:
        logger.warning("YooKassa credentials not configured")
        return None
    try:
        idempotency_key = str(uuid.uuid4())
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.yookassa.ru/v3/payments",
                auth=(settings.yukassa_shop_id, settings.yukassa_secret_key),
                headers={"Idempotence-Key": idempotency_key},
                json={
                    "amount": {
                        "value": f"{settings.subscription_price}.00",
                        "currency": "RUB",
                    },
                    "confirmation": {
                        "type": "redirect",
                        "return_url": f"https://t.me/{settings.crm_bot_username}",
                    },
                    "metadata": {"tenant_id": str(tenant_id)},
                    "description": (
                        f"TelegramCRM подписка {settings.subscription_days} дней"
                        f" — {company_name}"
                    ),
                    "capture": True,
                },
            )
        data = response.json()
        if response.status_code != 200:
            logger.error(f"YooKassa error status={response.status_code}: {data}")
            return None

        yukassa_id = data["id"]
        payment_url = data["confirmation"]["confirmation_url"]

        async with AsyncSessionLocal() as session:
            repo = TenantRepository(session)
            await repo.create_payment(
                tenant_id=tenant_id,
                amount=settings.subscription_price,
                yukassa_id=yukassa_id,
                period_days=settings.subscription_days,
            )
            await session.commit()

        return payment_url

    except Exception as e:
        logger.error(f"create_yukassa_payment failed tenant_id={tenant_id}: {e}")
        return None


async def _create_yukassa_payment(tenant_id: int) -> str | None:
    return await create_yukassa_payment(tenant_id, "")
