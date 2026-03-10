import json
from decimal import Decimal, InvalidOperation

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


def _parse_amount(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


async def _verify_yukassa_payment(yukassa_id: str) -> dict | None:
    """Verify payment status via YooKassa API and return payment payload."""
    if not settings.yukassa_shop_id or not settings.yukassa_secret_key:
        if IS_DEV_MODE:
            logger.warning(
                f"yukassa verify skipped in dev mode: missing credentials, payment={yukassa_id}"
            )
            return {"status": "succeeded"}
        logger.error(
            f"yukassa verify failed closed: missing credentials in non-dev mode, payment={yukassa_id}"
        )
        return None

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
            return None
        data = response.json()
        if data.get("status") != "succeeded":
            logger.warning(
                f"yukassa verify rejected: payment={yukassa_id} status={data.get('status')}"
            )
            return None
        return data
    except Exception as e:
        logger.error(f"yukassa verify exception: payment={yukassa_id} error={e}")
        return None


def _request_ip(request: Request) -> str:
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

    verified_payment = await _verify_yukassa_payment(yukassa_id)
    if not verified_payment:
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

            amount_data = verified_payment.get("amount") if isinstance(verified_payment, dict) else {}
            payment_amount = _parse_amount(amount_data.get("value") if isinstance(amount_data, dict) else None)
            db_amount = _parse_amount(payment.amount)
            expected_amount = _parse_amount(settings.subscription_price)
            currency = (amount_data.get("currency") if isinstance(amount_data, dict) else "") or ""
            metadata = verified_payment.get("metadata") if isinstance(verified_payment, dict) else {}
            metadata_tenant_raw = metadata.get("tenant_id") if isinstance(metadata, dict) else None
            try:
                metadata_tenant_id = int(metadata_tenant_raw)
            except (TypeError, ValueError):
                metadata_tenant_id = None

            if payment_amount is None or db_amount is None or expected_amount is None:
                logger.warning(
                    f"yukassa webhook rejected: invalid amount format payment={yukassa_id}"
                )
                return {"status": "ok"}
            if payment_amount != expected_amount:
                logger.warning(
                    f"yukassa webhook rejected: amount mismatch with plan payment={yukassa_id} got={payment_amount} expected={expected_amount}"
                )
                return {"status": "ok"}
            if payment_amount != db_amount:
                logger.warning(
                    f"yukassa webhook rejected: amount mismatch with db payment={yukassa_id} got={payment_amount} db={db_amount}"
                )
                return {"status": "ok"}
            if currency.upper() != "RUB":
                logger.warning(
                    f"yukassa webhook rejected: currency mismatch payment={yukassa_id} currency={currency!r}"
                )
                return {"status": "ok"}
            if metadata_tenant_id != payment.tenant_id:
                logger.warning(
                    f"yukassa webhook rejected: tenant metadata mismatch payment={yukassa_id} metadata_tenant_id={metadata_tenant_id} db_tenant_id={payment.tenant_id}"
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
