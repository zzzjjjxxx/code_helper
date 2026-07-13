from __future__ import annotations

from assistant_shared.models import TaskStatus

ALLOWED_STATUS_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.created: {TaskStatus.queued, TaskStatus.cancelled},
    TaskStatus.queued: {TaskStatus.reading, TaskStatus.failed, TaskStatus.succeeded, TaskStatus.cancelled},
    TaskStatus.reading: {TaskStatus.analyzing, TaskStatus.failed, TaskStatus.succeeded, TaskStatus.cancelled},
    TaskStatus.analyzing: {TaskStatus.patching, TaskStatus.failed, TaskStatus.succeeded, TaskStatus.cancelled},
    TaskStatus.patching: {TaskStatus.testing, TaskStatus.failed, TaskStatus.succeeded, TaskStatus.cancelled},
    TaskStatus.testing: {TaskStatus.awaiting_review, TaskStatus.succeeded, TaskStatus.failed, TaskStatus.cancelled},
    TaskStatus.awaiting_review: {
        TaskStatus.queued,
        TaskStatus.analyzing,
        TaskStatus.patching,
        TaskStatus.testing,
        TaskStatus.rolled_back,
        TaskStatus.succeeded,
        TaskStatus.failed,
    },
    TaskStatus.rolled_back: {TaskStatus.queued, TaskStatus.failed},
    TaskStatus.succeeded: {TaskStatus.rolled_back},
    TaskStatus.failed: {TaskStatus.queued, TaskStatus.cancelled, TaskStatus.rolled_back},
    TaskStatus.cancelled: set(),
}


def advance_status(current: TaskStatus, next_status: TaskStatus) -> TaskStatus:
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current, set())
    if next_status not in allowed:
        raise ValueError(f"Cannot transition from {current} to {next_status}")
    return next_status
