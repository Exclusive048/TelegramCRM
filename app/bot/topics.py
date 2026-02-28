from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.db.models.lead import LeadStatus


class TopicKey(str, Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    PAID = "PAID"
    SUCCESS = "SUCCESS"
    REJECTED = "REJECTED"
    GENERAL = "GENERAL"
    REMINDERS = "REMINDERS"
    CABINET = "CABINET"
    MANAGERS = "MANAGERS"
    KNOWLEDGE = "KNOWLEDGE"


@dataclass(frozen=True)
class TopicSpec:
    key: TopicKey
    title: str


TOPIC_SPECS: list[TopicSpec] = [
    TopicSpec(TopicKey.NEW, "📥 Лиды"),
    TopicSpec(TopicKey.IN_PROGRESS, "🛠 В работе"),
    TopicSpec(TopicKey.PAID, "💳 Оплачено"),
    TopicSpec(TopicKey.SUCCESS, "🏆 Успех"),
    TopicSpec(TopicKey.REJECTED, "❌ Отклонено"),
    TopicSpec(TopicKey.GENERAL, "💬 Общий чат"),
    TopicSpec(TopicKey.REMINDERS, "🔔 Напоминания"),
    TopicSpec(TopicKey.CABINET, "🗂 Кабинет"),
    TopicSpec(TopicKey.MANAGERS, "👥 Чат менеджеров"),
    TopicSpec(TopicKey.KNOWLEDGE, "📚 База знаний"),
]


STATUS_TO_TOPIC_KEY: dict[LeadStatus, TopicKey] = {
    LeadStatus.NEW: TopicKey.NEW,
    LeadStatus.IN_PROGRESS: TopicKey.IN_PROGRESS,
    LeadStatus.PAID: TopicKey.PAID,
    LeadStatus.SUCCESS: TopicKey.SUCCESS,
    LeadStatus.REJECTED: TopicKey.REJECTED,
}
