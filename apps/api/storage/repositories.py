from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from assistant_shared.models import (
    ArtifactRecord,
    MemoryRecord,
    RetrievalHit,
    SnapshotRecord,
    TaskDetail,
    TaskEventModel,
    TaskStatus,
    TaskSummary,
    TestOutcome,
)


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.utcnow()
    return datetime.fromisoformat(value)


def _decode_json(value: str | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


def row_to_summary(row: dict[str, Any]) -> TaskSummary:
    return TaskSummary(
        id=row["id"],
        title=row["title"],
        description=row["description"] or "",
        repository_path=row["repository_path"],
        status=TaskStatus(row["status"]),
        current_step=row["current_step"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
        summary=row["summary"],
        last_error=row["last_error"],
    )


def row_to_detail(
    row: dict[str, Any],
    *,
    events: list[TaskEventModel],
    snapshots: list[SnapshotRecord],
    memory: list[MemoryRecord],
) -> TaskDetail:
    latest_test_result_raw = _decode_json(row["latest_test_result"], None)
    latest_test_result = TestOutcome.model_validate(latest_test_result_raw) if latest_test_result_raw else None
    latest_retrieval_raw = _decode_json(row["latest_retrieval"], [])
    latest_retrieval = [RetrievalHit.model_validate(hit) for hit in latest_retrieval_raw]
    return TaskDetail(
        id=row["id"],
        title=row["title"],
        description=row["description"] or "",
        repository_path=row["repository_path"],
        status=TaskStatus(row["status"]),
        current_step=row["current_step"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
        summary=row["summary"],
        last_error=row["last_error"],
        focus_paths=_decode_json(row["focus_paths"], []),
        latest_diff=row["latest_diff"],
        latest_test_result=latest_test_result,
        latest_retrieval=latest_retrieval,
        snapshots=snapshots,
        memory=memory,
        events=events,
    )


def row_to_event(row: dict[str, Any]) -> TaskEventModel:
    return TaskEventModel(
        id=row["id"],
        event_id=row["event_id"],
        task_id=row["task_id"],
        sequence=row["sequence"],
        type=row["type"],
        step=row["step"],
        message=row["message"],
        payload=_decode_json(row["payload"], {}),
        created_at=_parse_datetime(row["created_at"]),
    )


def row_to_snapshot(row: dict[str, Any]) -> SnapshotRecord:
    return SnapshotRecord(
        id=row["id"],
        task_id=row["task_id"],
        label=row["label"],
        path=row["path"],
        created_at=_parse_datetime(row["created_at"]),
    )


def row_to_artifact(row: dict[str, Any]) -> ArtifactRecord:
    return ArtifactRecord(
        id=row["id"],
        artifact_id=row["artifact_id"],
        task_id=row["task_id"],
        type=row["type"],
        name=row["name"],
        content=row["content"] or "",
        created_at=_parse_datetime(row["created_at"]),
    )


def row_to_memory(row: dict[str, Any]) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        memory_id=row["memory_id"],
        task_id=row["task_id"],
        kind=row["kind"],
        title=row["title"],
        content=row["content"],
        keywords=_decode_json(row["keywords"], []),
        related_files=_decode_json(row["related_files"], []),
        created_at=_parse_datetime(row["created_at"]),
    )


def encode_json(value: Any) -> str | None:
    if value is None:
        return None

    def _jsonable(item: Any) -> Any:
        if hasattr(item, "model_dump"):
            return _jsonable(item.model_dump(mode="json"))
        if isinstance(item, dict):
            return {key: _jsonable(inner) for key, inner in item.items()}
        if isinstance(item, list):
            return [_jsonable(inner) for inner in item]
        if isinstance(item, tuple):
            return [_jsonable(inner) for inner in item]
        return item

    return json.dumps(_jsonable(value), ensure_ascii=False)
