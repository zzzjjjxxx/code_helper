from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import AsyncGenerator

from assistant_shared.models import TaskEventModel, TaskStatus, TaskStep
from assistant_telemetry.events import format_sse, new_event

from apps.api.storage.sqlite import SQLiteStore


class EventService:
    def __init__(self, store: SQLiteStore):
        self.store = store
        self._subscribers: dict[str, set[asyncio.Queue[TaskEventModel]]] = defaultdict(set)

    async def emit(
        self,
        *,
        task_id: str,
        event_type: str,
        step: TaskStep | str | None,
        message: str,
        payload: dict | None = None,
    ) -> TaskEventModel:
        event = new_event(
            task_id=task_id,
            sequence=0,
            event_type=event_type,
            step=step,
            message=message,
            payload=payload or {},
        )
        stored = await asyncio.to_thread(self.store.append_event, event)
        for queue in list(self._subscribers.get(task_id, set())):
            queue.put_nowait(stored)
        return stored

    def subscribe(self, task_id: str) -> asyncio.Queue[TaskEventModel]:
        queue: asyncio.Queue[TaskEventModel] = asyncio.Queue()
        self._subscribers[task_id].add(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[TaskEventModel]) -> None:
        subscribers = self._subscribers.get(task_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(task_id, None)

    async def stream(self, task_id: str, after_sequence: int = 0) -> AsyncGenerator[str, None]:
        queue = self.subscribe(task_id)
        try:
            historical = await asyncio.to_thread(self.store.list_events, task_id, after_sequence)
            for event in historical:
                yield format_sse(event)
            task = await asyncio.to_thread(self.store.get_task, task_id)
            if task.status in {TaskStatus.succeeded, TaskStatus.failed, TaskStatus.rolled_back, TaskStatus.cancelled}:
                return
            while True:
                event = await queue.get()
                yield format_sse(event)
        finally:
            self.unsubscribe(task_id, queue)
