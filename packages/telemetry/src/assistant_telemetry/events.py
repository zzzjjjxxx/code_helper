from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4
from enum import Enum

from assistant_shared.models import TaskEventModel, TaskStep


def new_event(
    *,
    task_id: str,
    sequence: int,
    event_type: str,
    step: TaskStep | str | None,
    message: str,
    payload: dict | None = None,
) -> TaskEventModel:
    return TaskEventModel(
        event_id=str(uuid4()),
        task_id=task_id,
        sequence=sequence,
        type=event_type,
        step=step.value if isinstance(step, Enum) else (str(step) if step is not None else None),
        message=message,
        payload=payload or {},
        created_at=datetime.utcnow(),
    )


def format_sse(event: TaskEventModel) -> str:
    payload = event.model_dump(mode="json")
    return "\n".join(
        [
            f"id: {event.sequence}",
            f"event: {event.type}",
            f"data: {json.dumps(payload, ensure_ascii=False)}",
            "",
            "",
        ]
    )
