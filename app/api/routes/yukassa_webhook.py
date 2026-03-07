import json

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.api.rate_limit import limiter
from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.db.repositories.tenant_repository import TenantRepository

router = APIRouter(prefix="/webhook", tags=["Webhooks"])
IS_DEV_MODE = bool(getattr(settings, "debug", False))


async def _verify_yukassa_payment(yukassa_id: str) -> bool:
    """Verify payment status via YooKassa API."""
    if not settings.yukassa_shop_id or not settings.yukassa_secret_key:
        if IS_DEV_MODE:
            logger.warning(
                f"yukassa verify skipped in dev mode: missing credentials, payment={yukassa_id}"
            )
            return True
        logger.error(
            f"yukassa verify failed closed: missing credentials in non-dev mode, payment={yukassa_id}"
        )
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"https://api.yookassa.ru/v3/payments/{yukassa_id}",
                auth=(settings.yukassa_shop_id, settings.yukassa_secret_key),
            )
        if response.status_code != 200:
            logger.error(
                f"yukassa verify failed: payment={yukassa_id} status_code={response.status_code}"
            )
            return False
        data = response.json()
        is_valid = data.get("status") == "succeeded"
        if not is_valid:
            logger.warning(
                f"yukassa verify rejected: payment={yukassa_id} status={data.get('status')}"
            )
        return is_valid
    except Exception as e:
        logger.error(f"yukassa verify exception: payment={yukassa_id} error={e}")
        return False


def _request_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


@router.post("/yukassa")
@limiter.limit("10/minute")
async def yukassa_webhook(request: Request):
    whitelist = settings.yukassa_ip_whitelist_set
    if whitelist:
        source_ip = _request_ip(request)
        if source_ip not in whitelist:
            logger.warning(
                f"yukassa webhook rejected: ip={source_ip!r} not in whitelist"
            )
            return JSONResponse(
                status_code=403,
                content={"status": "forbidden"},
            )

    try:
        data = json.loads(await request.body())
    except Exception:
        logger.warning("yukassa webhook rejected: invalid JSON")
        return {"status": "ok"}

    event = data.get("event")
    if event != "payment.succeeded":
        logger.info(f"yukassa webhook ignored: event={event!r}")
        return {"status": "ok"}

    obj = data.get("object", {})
    yukassa_id = obj.get("id")
    if not yukassa_id:
        logger.warning("yukassa webhook rejected: missing payment id")
        return {"status": "ok"}

    is_real = await _verify_yukassa_payment(yukassa_id)
    if not is_real:
        logger.warning(f"yukassa webhook rejected by verify: payment={yukassa_id}")
        return {"status": "ok"}

    try:
        async with AsyncSessionLocal() as session:
            from master_bot.notify import notify_admin, notify_tenant_owner

            repo = TenantRepository(session)
            payment = await repo.get_payment_by_yukassa_id(yukassa_id)
            if not payment:
                logger.warning(
                    f"yukassa webhook ignored: payment row not found, payment={yukassa_id}"
                )
                return {"status": "ok"}

            tenant_id = payment.tenant_id
            updated_payment = await repo.mark_payment_succeeded(yukassa_id)
            if not updated_payment:
                logger.info(
                    f"yukassa webhook duplicate/non-pending ignored: payment={yukassa_id} status={payment.status}"
                )
                return {"status": "ok"}

            new_until, api_key = await repo.activate_subscription(
                tenant_id,
                days=settings.subscription_days,
            )
            await session.commit()
            tenant = await repo.get_by_id(tenant_id)
    except Exception as e:
        logger.exception(f"yukassa webhook processing failed: payment={yukassa_id} error={e}")
        return {"status": "ok"}

    try:
        logger.info(f"yukassa payment applied: tenant_id={tenant_id} until={new_until}")
        await notify_admin(
            f"💰 Оплата: {settings.subscription_price} руб\n"
            f"🏢: {tenant.company_name if tenant else tenant_id}\n"
            f"📅 Подписка до: {new_until.strftime('%d.%m.%Y')}"
        )
        if tenant:
            await notify_tenant_owner(
                tenant.owner_tg_id,
                "💰 Оплата получена.\n\n"
                f"Подписка активна до {new_until.strftime('%d.%m.%Y')}.\n\n"
                f"Ваш API-ключ:\n{api_key}\n\n"
                "Используйте /start для инструкций по настройке.",
            )
    except Exception as e:
        logger.exception(f"yukassa webhook notify failed: payment={yukassa_id} error={e}")
        return {"status": "ok"}

    return {"status": "ok"}
