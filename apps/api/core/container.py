from __future__ import annotations

from dataclasses import dataclass

from apps.api.core.config import Settings
from apps.api.services.event_service import EventService
from apps.api.services.rollback_service import RollbackService
from apps.api.services.task_service import TaskService
from apps.api.storage.sqlite import SQLiteStore


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    store: SQLiteStore
    events: EventService
    rollback_service: RollbackService
    task_service: TaskService
