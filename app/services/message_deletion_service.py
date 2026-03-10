from __future__ import annotations

import asyncio
import heapq
import json
import time
from dataclasses import dataclass

from loguru import logger

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - optional dependency at runtime
    Redis = None  # type: ignore

from app.telegram.safe_sender import TelegramSafeSender


@dataclass(slots=True)
class DeleteJob:
    chat_id: int
    message_id: int
    thread_id: int | None
    delete_at: float


class MessageDeletionService:
    def __init__(
        self,
        redis: "Redis | None" = None,
        *,
        key: str = "telegram:delete_jobs",
        poll_interval: float = 1.0,
        batch_size: int = 200,
    ) -> None:
        self._redis = redis
        self._key = key
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._task: asyncio.Task | None = None
        self._sender: TelegramSafeSender | None = None
        self._heap: list[tuple[float, str]] = []
        self._jobs: dict[str, DeleteJob] = {}
        self._lock = asyncio.Lock()

    async def start(self, sender: TelegramSafeSender) -> None:
        if self._task is not None:
            return
        self._sender = sender
        self._task = asyncio.create_task(self._run_forever())
        logger.info("message_deletion_service_started redis={}", bool(self._redis))

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("message_deletion_service_stopped")

    async def schedule(
        self,
        *,
        chat_id: int,
        message_id: int,
        delete_at: float,
        thread_id: int | None = None,
    ) -> None:
        if self._redis is not None:
            payload = json.dumps(
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "thread_id": thread_id,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
            await self._redis.zadd(self._key, {payload: float(delete_at)})
            logger.debug(
                "delete_job_scheduled redis chat_id={} message_id={} delete_at={}",
                chat_id,
                message_id,
                delete_at,
            )
            return

        key = f"{chat_id}:{message_id}:{thread_id or 0}"
        async with self._lock:
            self._jobs[key] = DeleteJob(
                chat_id=chat_id,
                message_id=message_id,
                thread_id=thread_id,
                delete_at=delete_at,
            )
            heapq.heappush(self._heap, (delete_at, key))
        logger.debug(
            "delete_job_scheduled memory chat_id={} message_id={} delete_at={}",
            chat_id,
            message_id,
            delete_at,
        )

    async def _run_forever(self) -> None:
        while True:
            try:
                await self._run_loop()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "message_deletion_service_loop_crashed; restarting in 5s"
                )
                await asyncio.sleep(5)

    async def _run_loop(self) -> None:
        while True:
            now = time.time()
            if self._redis is not None:
                await self._drain_redis(now)
            else:
                await self._drain_memory(now)
            await asyncio.sleep(self._poll_interval)

    async def _drain_redis(self, now: float) -> None:
        if self._sender is None or self._redis is None:
            return
        members = await self._redis.zrangebyscore(
            self._key,
            "-inf",
            now,
            start=0,
            num=self._batch_size,
        )
        if not members:
            return
        for raw in members:
            payload = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            try:
                data = json.loads(payload)
                job = DeleteJob(
                    chat_id=int(data["chat_id"]),
                    message_id=int(data["message_id"]),
                    thread_id=data.get("thread_id"),
                    delete_at=now,
                )
            except Exception as e:
                logger.debug("delete_job_parse_failed payload={} err={}", payload, e)
                await self._redis.zrem(self._key, raw)
                continue
            await self._execute_job(job)
            await self._redis.zrem(self._key, raw)

    async def _drain_memory(self, now: float) -> None:
        if self._sender is None:
            return
        due_keys: list[str] = []
        async with self._lock:
            while self._heap and self._heap[0][0] <= now:
                _, key = heapq.heappop(self._heap)
                if key in self._jobs:
                    due_keys.append(key)
        for key in due_keys:
            async with self._lock:
                job = self._jobs.pop(key, None)
            if job:
                await self._execute_job(job)

    async def _execute_job(self, job: DeleteJob) -> None:
        if self._sender is None:
            return
        try:
            await self._sender.delete_message(
                chat_id=job.chat_id,
                message_id=job.message_id,
                thread_id=job.thread_id,
            )
            logger.debug(
                "delete_job_done chat_id={} message_id={}",
                job.chat_id,
                job.message_id,
            )
        except Exception as e:
            logger.debug(
                "delete_job_failed chat_id={} message_id={} err={}",
                job.chat_id,
                job.message_id,
                e,
            )
