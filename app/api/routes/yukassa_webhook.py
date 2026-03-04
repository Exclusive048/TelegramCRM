import json

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
import httpx

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.repositories.tenant_repository import TenantRepository

router = APIRouter(prefix="/webhook", tags=["Webhooks"])


async def _verify_yukassa_payment(yukassa_id: str) -> bool:
    """
    Верифицирует платёж через API ЮКасса.
    Защита от фейковых webhook-запросов.
    """
    if not settings.yukassa_shop_id or not settings.yukassa_secret_key:
        # Если ЮКасса не настроена — пропустить верификацию (dev режим)
        logger.warning("YooKassa not configured, skipping verification")
        return True
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"https://api.yookassa.ru/v3/payments/{yukassa_id}",
                auth=(settings.yukassa_shop_id, settings.yukassa_secret_key),
            )
        if response.status_code != 200:
            logger.error(f"yukassa verify failed status={response.status_code}")
            return False
        data = response.json()
        is_valid = data.get("status") == "succeeded"
        if not is_valid:
            logger.warning(
                f"yukassa verify: payment {yukassa_id} status={data.get('status')}"
            )
        return is_valid
    except Exception as e:
        logger.error(f"yukassa verify exception: {e}")
        return False


@router.post("/yukassa")
async def yukassa_webhook(request: Request):
    try:
        data = json.loads(await request.body())
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    if data.get("event") != "payment.succeeded":
        return {"status": "ok"}

    obj = data.get("object", {})
    yukassa_id = obj.get("id")
    tenant_id_raw = (obj.get("metadata") or {}).get("tenant_id")

    if not yukassa_id or not tenant_id_raw:
        raise HTTPException(400, "Missing required fields")

    # ВЕРИФИКАЦИЯ — обязательно!
    is_real = await _verify_yukassa_payment(yukassa_id)
    if not is_real:
        logger.warning(f"yukassa fake webhook rejected: {yukassa_id}")
        # Возвращаем 200 чтобы YooKassa не повторяла запрос
        return {"status": "ok"}

    tenant_id = int(tenant_id_raw)

    async with AsyncSessionLocal() as session:
        from master_bot.notify import notify_admin, notify_tenant_owner

        repo = TenantRepository(session)

        existing = await repo.get_payment_by_yukassa_id(yukassa_id)
        if existing and existing.status == "succeeded":
            logger.info(f"yukassa duplicate ignored: {yukassa_id}")
            return {"status": "ok"}

        await repo.mark_payment_succeeded(yukassa_id)
        new_until, api_key = await repo.activate_subscription(
            tenant_id,
            days=settings.subscription_days,
        )
        await session.commit()

        tenant = await repo.get_by_id(tenant_id)

    logger.info(f"yukassa payment ok tenant_id={tenant_id} until={new_until}")

    await notify_admin(
        f"💰 Оплата {settings.subscription_price} руб\n"
        f"🏢 <b>{tenant.company_name if tenant else tenant_id}</b>\n"
        f"📅 Подписка до: {new_until.strftime('%d.%m.%Y')}"
    )

    if tenant:
        await notify_tenant_owner(
            tenant.owner_tg_id,
            f"✅ <b>Оплата прошла!</b>\n\n"
            f"Подписка активирована до {new_until.strftime('%d.%m.%Y')}.\n\n"
            f"🔑 Ваш API ключ:\n<code>{api_key}</code>\n\n"
            f"Если нужна полная инструкция — напишите /start",
        )

    return {"status": "ok"}
