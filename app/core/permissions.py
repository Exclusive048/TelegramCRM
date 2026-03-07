"""
Проверки прав доступа.

Логика ролей:
  - TG-владелец/админ группы + запись в managers с role=admin → CRM-ADMIN
  - Запись в managers с role=manager → МЕНЕДЖЕР
  - Все остальные → нет доступа

Важно: TG-статус (владелец/админ группы) проверяем через get_chat_member.
Запись в БД нужна чтобы бот знал кто из TG-админов явно назначен в CRM.
"""
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from app.db.repositories.lead_repository import LeadRepository
from app.db.models.lead import Manager, ManagerRole
from app.telegram.safe_sender import TelegramSafeSender
import logging

log = logging.getLogger(__name__)

async def get_tg_role(sender: TelegramSafeSender, chat_id: int, user_id: int) -> ChatMemberStatus | None:
    """
    Возвращает: 'creator' | 'administrator' | 'member' | None
    """
    try:
        member = await sender.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status

    except TelegramBadRequest as e:
        # пользователь не участник / неправильный chat_id
        log.debug(
            "getChatMember bad request",
            extra={"chat_id": chat_id, "user_id": user_id, "error": str(e)},
        )
        return None

    except TelegramRetryAfter:
        # инфраструктура, можно пробросить выше
        raise

    except Exception:
        # неожиданный баг — логируем ОБЯЗАТЕЛЬНО
        log.exception(
            "Unexpected error in get_tg_role",
            extra={"chat_id": chat_id, "user_id": user_id},
        )
        return None


async def is_tg_admin(sender: TelegramSafeSender, chat_id: int, user_id: int) -> bool:
    status = await get_tg_role(sender, chat_id, user_id)
    if status is None:
        return False
    return status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)


async def get_manager(repo: LeadRepository, tg_id: int) -> Manager | None:
    """Получить менеджера из БД"""
    return await repo.get_manager_by_tg_id(tg_id)


async def is_any_manager(repo: LeadRepository, tg_id: int) -> bool:
    """Зарегистрирован ли пользователь как менеджер (любая роль)"""
    m = await get_manager(repo, tg_id)
    return m is not None and m.is_active


async def is_crm_admin(
    sender: TelegramSafeSender,
    repo: LeadRepository,
    chat_id: int,
    tg_id: int,
    tenant_id: int | None = None,
) -> bool:
    """
    CRM-админ = TG-админ группы И запись в managers с role=admin.
    Владелец группы всегда считается CRM-админом если он в managers.
    """
    if not await is_tg_admin(sender, chat_id, tg_id):
        return False
    m = await get_manager(repo, tg_id)
    if m is None or not m.is_active:
        return False
    if tenant_id is not None and m.tenant_id != tenant_id:
        return False
    return m.role == ManagerRole.ADMIN
