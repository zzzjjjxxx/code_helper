from __future__ import annotations

import asyncio
from pathlib import Path

from assistant_shared.models import RollbackResponse, TaskStep, TaskStatus
from assistant_tools import restore_snapshot
from assistant_telemetry.events import new_event

from apps.api.services.event_service import EventService
from apps.api.storage.sqlite import SQLiteStore


class RollbackService:
    def __init__(self, store: SQLiteStore, events: EventService):
        self.store = store
        self.events = events

    async def rollback(self, task_id: str) -> RollbackResponse:
        task = await asyncio.to_thread(self.store.get_task_detail, task_id)
        snapshot = await asyncio.to_thread(self.store.get_latest_snapshot, task_id)
        if snapshot is None:
            raise RuntimeError("No snapshot is available for rollback")

        await self.events.emit(
            task_id=task_id,
            event_type="rollback.started",
            step=TaskStep.rollback,
            message=f"Restoring snapshot {snapshot.id}",
            payload={"snapshot_id": snapshot.id, "snapshot_path": snapshot.path},
        )

        await asyncio.to_thread(restore_snapshot, snapshot.path, task.repository_path)
        await asyncio.to_thread(
            self.store.update_task,
            task_id,
            status=TaskStatus.rolled_back,
            current_step=TaskStep.rollback.value,
            summary=f"Restored snapshot {snapshot.id}",
            last_error=None,
        )

        await self.events.emit(
            task_id=task_id,
            event_type="rollback.completed",
            step=TaskStep.rollback,
            message="Rollback completed successfully",
            payload={"snapshot_id": snapshot.id, "snapshot_path": snapshot.path},
        )

        restored_task = await asyncio.to_thread(self.store.get_task, task_id)
        return RollbackResponse(task=restored_task, restored_snapshot_id=snapshot.id, message="Rollback completed")
