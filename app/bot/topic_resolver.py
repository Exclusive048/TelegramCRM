from __future__ import annotations

from app.bot.constants.ttl import TTL_ERROR_SEC
from app.bot.topic_cache import get_cached, set_cached
from app.bot.topics import TopicKey
from app.db.repositories.tenant_topics import TenantTopicRepository
from app.telegram.safe_sender import TelegramSafeSender


async def resolve_topic_thread_id(
    chat_id: int,
    key: TopicKey,
    session,
    *,
    sender: TelegramSafeSender | None = None,
    thread_id: int | None = None,
) -> int | None:
    cached = get_cached(chat_id)
    if cached is not None:
        thread = cached.get(key.value)
        if thread is not None:
            return thread

    repo = TenantTopicRepository(session)
    mapping = await repo.get_topic_map(chat_id)
    if mapping:
        set_cached(chat_id, mapping)
        thread = mapping.get(key.value)
    else:
        thread = None

    if thread is None and sender is not None:
        await sender.send_ephemeral_text(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text="⚠️ Сначала выполните /setup в этом чате.",
            ttl_sec=TTL_ERROR_SEC,
        )
    return thread
