from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram.types import CallbackQuery, Message


@dataclass(frozen=True, slots=True)
class MessageRef:
    chat_id: int
    message_id: int
    topic_id: int | None
    source: str

    def __str__(self) -> str:
        return (
            f"chat_id={self.chat_id} "
            f"topic_id={self.topic_id} "
            f"message_id={self.message_id} "
            f"source={self.source}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "topic_id": self.topic_id,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "MessageRef | None":
        if not data:
            return None
        topic_id = data.get("topic_id")
        if topic_id is not None:
            topic_id = int(topic_id)
        return cls(
            chat_id=int(data["chat_id"]),
            message_id=int(data["message_id"]),
            topic_id=topic_id,
            source=str(data.get("source") or "state"),
        )

    @classmethod
    def from_message(cls, message: Message, *, source: str = "message") -> "MessageRef":
        return cls(
            chat_id=message.chat.id,
            message_id=message.message_id,
            topic_id=message.message_thread_id,
            source=source,
        )

    @classmethod
    def from_callback(cls, callback: CallbackQuery) -> "MessageRef | None":
        if not callback.message:
            return None
        return cls.from_message(callback.message, source="callback")

    @classmethod
    def from_reply(cls, reply: Message | None) -> "MessageRef | None":
        if not reply:
            return None
        return cls.from_message(reply, source="reply")
