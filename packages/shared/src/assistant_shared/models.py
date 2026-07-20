from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    created = "created"
    queued = "queued"
    reading = "reading"
    analyzing = "analyzing"
    patching = "patching"
    testing = "testing"
    awaiting_review = "awaiting_review"
    rolled_back = "rolled_back"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class TaskStep(str, Enum):
    read = "read"
    analyze = "analyze"
    patch = "patch"
    test = "test"
    review = "review"
    summarize = "summarize"
    rollback = "rollback"


class SubgoalStatus(str, Enum):
    planned = "planned"
    active = "active"
    completed = "completed"
    blocked = "blocked"
    skipped = "skipped"


class TaskCreateRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    repository_path: str | None = None
    focus_paths: list[str] = Field(default_factory=list)


class CommandRule(BaseModel):
    executables: list[str] = Field(default_factory=list)
    args_prefix: list[str] = Field(default_factory=list)


class SecurityPolicy(BaseModel):
    workspace_root: str
    snapshot_root: str
    allowed_commands: list[CommandRule] = Field(default_factory=list)
    snapshot_integrity_required: bool = True


class RetrievalHit(BaseModel):
    path: str
    score: float
    reason: str
    preview: str = ""


class MemoryRecord(BaseModel):
    id: int | None = None
    memory_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    kind: str
    title: str
    content: str
    keywords: list[str] = Field(default_factory=list)
    related_files: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.utcnow())


class TaskSubgoalRecord(BaseModel):
    id: int | None = None
    subgoal_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    position: int = 0
    phase: str
    title: str
    description: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    files_to_read: list[str] = Field(default_factory=list)
    rationale: str = ""
    status: SubgoalStatus = SubgoalStatus.planned
    created_at: datetime = Field(default_factory=lambda: datetime.utcnow())
    updated_at: datetime = Field(default_factory=lambda: datetime.utcnow())
    completed_at: datetime | None = None


class TaskEventModel(BaseModel):
    id: int | None = None
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    sequence: int = 0
    type: str
    step: str | None = None
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.utcnow())


class SnapshotRecord(BaseModel):
    id: str
    task_id: str
    label: str
    path: str
    created_at: datetime


class ArtifactRecord(BaseModel):
    id: int | None = None
    artifact_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    type: str
    name: str
    content: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.utcnow())


class TestOutcome(BaseModel):
    command: str
    return_code: int
    stdout: str = ""
    stderr: str = ""
    passed: bool = False
    duration_ms: int = 0


class PatchArtifact(BaseModel):
    path: str
    before: str
    after: str
    diff: str


class TaskSummary(BaseModel):
    id: str
    title: str
    description: str = ""
    repository_path: str
    status: TaskStatus
    current_step: str | None = None
    created_at: datetime
    updated_at: datetime
    summary: str | None = None
    last_error: str | None = None


class TaskDetail(TaskSummary):
    focus_paths: list[str] = Field(default_factory=list)
    latest_diff: str | None = None
    latest_test_result: TestOutcome | None = None
    latest_retrieval: list[RetrievalHit] = Field(default_factory=list)
    subgoals: list[TaskSubgoalRecord] = Field(default_factory=list)
    snapshots: list[SnapshotRecord] = Field(default_factory=list)
    memory: list[MemoryRecord] = Field(default_factory=list)
    events: list[TaskEventModel] = Field(default_factory=list)


class TaskRunResponse(BaseModel):
    task: TaskSummary
    accepted: bool = True


class RollbackResponse(BaseModel):
    task: TaskSummary
    restored_snapshot_id: str | None = None
    message: str
