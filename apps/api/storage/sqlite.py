from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from assistant_shared.models import ArtifactRecord, MemoryRecord, PatchArtifact, RetrievalHit, SnapshotRecord, SubgoalStatus, TaskCreateRequest, TaskDetail, TaskEventModel, TaskStatus, TaskSubgoalRecord, TaskSummary, TestOutcome
from assistant_telemetry.events import new_event

from apps.api.storage.repositories import encode_json, row_to_artifact, row_to_detail, row_to_event, row_to_memory, row_to_snapshot, row_to_subgoal, row_to_summary

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    repository_path TEXT NOT NULL,
    focus_paths TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    current_step TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    summary TEXT,
    latest_diff TEXT,
    latest_retrieval TEXT,
    latest_test_result TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    type TEXT NOT NULL,
    step TEXT,
    message TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_task_events_task_sequence
    ON task_events(task_id, sequence);

CREATE TABLE IF NOT EXISTS task_snapshots (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    label TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS task_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_created_at
    ON task_artifacts(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    related_files TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_task_memory_task_created_at
    ON task_memory(task_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_memory_created_at
    ON task_memory(created_at DESC);

CREATE TABLE IF NOT EXISTS task_subgoals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subgoal_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    phase TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    success_criteria TEXT NOT NULL DEFAULT '[]',
    files_to_read TEXT NOT NULL DEFAULT '[]',
    rationale TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_task_subgoals_task_position
    ON task_subgoals(task_id, position);

CREATE UNIQUE INDEX IF NOT EXISTS idx_task_subgoals_task_subgoal
    ON task_subgoals(task_id, subgoal_id);
"""


class SQLiteStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_columns(connection)
            connection.commit()

    def create_task(
        self,
        *,
        title: str,
        description: str,
        repository_path: str,
        focus_paths: list[str],
    ) -> TaskSummary:
        task_id = str(uuid4())
        now = datetime.utcnow().isoformat()
        with self._write_connection() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    id, title, description, repository_path, focus_paths,
                    status, current_step, created_at, updated_at, started_at,
                    finished_at, summary, latest_diff, latest_retrieval, latest_test_result, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    title,
                    description,
                    repository_path,
                    encode_json(focus_paths) or "[]",
                    TaskStatus.created.value,
                    None,
                    now,
                    now,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
        return self.get_task(task_id)

    def list_tasks(self) -> list[TaskSummary]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
            return [row_to_summary(dict(row)) for row in rows]

    def get_task(self, task_id: str) -> TaskSummary:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Task '{task_id}' not found")
            return row_to_summary(dict(row))

    def delete_task(self, task_id: str) -> TaskSummary:
        task = self.get_task(task_id)
        with self._write_connection() as connection:
            connection.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM task_snapshots WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM task_artifacts WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM task_memory WHERE task_id = ?", (task_id,))
            connection.execute("DELETE FROM task_subgoals WHERE task_id = ?", (task_id,))
            cursor = connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            if cursor.rowcount == 0:
                raise KeyError(f"Task '{task_id}' not found")
        return task

    def get_task_detail(self, task_id: str) -> TaskDetail:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Task '{task_id}' not found")
            events = [row_to_event(dict(event_row)) for event_row in connection.execute(
                "SELECT * FROM task_events WHERE task_id = ? ORDER BY sequence ASC",
                (task_id,),
            ).fetchall()]
            snapshots = [row_to_snapshot(dict(snapshot_row)) for snapshot_row in connection.execute(
                "SELECT * FROM task_snapshots WHERE task_id = ? ORDER BY created_at DESC",
                (task_id,),
            ).fetchall()]
            memory = [row_to_memory(dict(memory_row)) for memory_row in connection.execute(
                "SELECT * FROM task_memory WHERE task_id = ? ORDER BY created_at DESC, id DESC",
                (task_id,),
            ).fetchall()]
            subgoals = [row_to_subgoal(dict(subgoal_row)) for subgoal_row in connection.execute(
                "SELECT * FROM task_subgoals WHERE task_id = ? ORDER BY position ASC, id ASC",
                (task_id,),
            ).fetchall()]
            return row_to_detail(dict(row), events=events, snapshots=snapshots, memory=memory, subgoals=subgoals)

    def update_task(self, task_id: str, **fields) -> TaskSummary:
        if not fields:
            return self.get_task(task_id)
        updates = []
        values = []
        for key, value in fields.items():
            updates.append(f"{key} = ?")
            values.append(self._normalize_value(value))
        updates.append("updated_at = ?")
        values.append(datetime.utcnow().isoformat())
        values.append(task_id)
        with self._write_connection() as connection:
            cursor = connection.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Task '{task_id}' not found")
        return self.get_task(task_id)

    def append_event(self, event: TaskEventModel) -> TaskEventModel:
        with self._write_connection() as connection:
            next_sequence = (
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM task_events WHERE task_id = ?",
                    (event.task_id,),
                ).fetchone()[0]
            )
            stored = event.model_copy(update={"sequence": next_sequence, "created_at": event.created_at or datetime.utcnow()})
            cursor = connection.execute(
                """
                INSERT INTO task_events (event_id, task_id, sequence, type, step, message, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.event_id,
                    stored.task_id,
                    stored.sequence,
                    stored.type,
                    stored.step,
                    stored.message,
                    encode_json(stored.payload) or "{}",
                    stored.created_at.isoformat(),
                ),
            )
            stored = stored.model_copy(update={"id": cursor.lastrowid})
        return stored

    def list_events(self, task_id: str, after_sequence: int = 0) -> list[TaskEventModel]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_events WHERE task_id = ? AND sequence > ? ORDER BY sequence ASC",
                (task_id, after_sequence),
            ).fetchall()
            return [row_to_event(dict(row)) for row in rows]

    def create_snapshot(self, *, task_id: str, label: str, path: str) -> SnapshotRecord:
        snapshot_id = str(uuid4())
        created_at = datetime.utcnow().isoformat()
        with self._write_connection() as connection:
            connection.execute(
                "INSERT INTO task_snapshots (id, task_id, label, path, created_at) VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, task_id, label, path, created_at),
            )
        return SnapshotRecord(id=snapshot_id, task_id=task_id, label=label, path=path, created_at=datetime.fromisoformat(created_at))

    def create_artifact(
        self,
        *,
        task_id: str,
        type: str,
        name: str,
        content: str,
    ) -> ArtifactRecord:
        artifact_id = str(uuid4())
        created_at = datetime.utcnow().isoformat()
        with self._write_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO task_artifacts (artifact_id, task_id, type, name, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, task_id, type, name, content, created_at),
            )
        return ArtifactRecord(
            id=cursor.lastrowid,
            artifact_id=artifact_id,
            task_id=task_id,
            type=type,
            name=name,
            content=content,
            created_at=datetime.fromisoformat(created_at),
        )

    def list_artifacts(self, task_id: str) -> list[ArtifactRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY created_at ASC, id ASC",
                (task_id,),
            ).fetchall()
            return [row_to_artifact(dict(row)) for row in rows]

    def create_memory(
        self,
        *,
        task_id: str,
        kind: str,
        title: str,
        content: str,
        keywords: list[str] | None = None,
        related_files: list[str] | None = None,
    ) -> MemoryRecord:
        memory_id = str(uuid4())
        created_at = datetime.utcnow().isoformat()
        with self._write_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO task_memory (
                    memory_id, task_id, kind, title, content, keywords, related_files, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    task_id,
                    kind,
                    title,
                    content,
                    encode_json(keywords or []) or "[]",
                    encode_json(related_files or []) or "[]",
                    created_at,
                ),
            )
        return MemoryRecord(
            id=cursor.lastrowid,
            memory_id=memory_id,
            task_id=task_id,
            kind=kind,
            title=title,
            content=content,
            keywords=keywords or [],
            related_files=related_files or [],
            created_at=datetime.fromisoformat(created_at),
        )

    def replace_subgoals(self, task_id: str, subgoals: list[dict[str, Any]]) -> list[TaskSubgoalRecord]:
        records: list[TaskSubgoalRecord] = []
        with self._write_connection() as connection:
            connection.execute("DELETE FROM task_subgoals WHERE task_id = ?", (task_id,))
            for position, subgoal in enumerate(subgoals):
                payload = dict(subgoal)
                payload["task_id"] = task_id
                payload["position"] = int(payload.get("position", position))
                payload["subgoal_id"] = str(payload.get("subgoal_id") or uuid4())
                payload["status"] = payload.get("status") or SubgoalStatus.planned.value
                record = TaskSubgoalRecord.model_validate(payload)
                cursor = connection.execute(
                    """
                    INSERT INTO task_subgoals (
                        subgoal_id, task_id, position, phase, title, description,
                        success_criteria, files_to_read, rationale, status,
                        created_at, updated_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.subgoal_id,
                        record.task_id,
                        record.position,
                        record.phase,
                        record.title,
                        record.description,
                        encode_json(record.success_criteria) or "[]",
                        encode_json(record.files_to_read) or "[]",
                        record.rationale,
                        record.status.value,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                        record.completed_at.isoformat() if record.completed_at else None,
                    ),
                )
                records.append(record.model_copy(update={"id": cursor.lastrowid}))
        return records

    def list_subgoals(self, task_id: str) -> list[TaskSubgoalRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_subgoals WHERE task_id = ? ORDER BY position ASC, id ASC",
                (task_id,),
            ).fetchall()
            return [row_to_subgoal(dict(row)) for row in rows]

    def update_subgoal(self, task_id: str, subgoal_id: str, **fields) -> TaskSubgoalRecord | None:
        if not fields:
            subgoals = self.list_subgoals(task_id)
            return next((subgoal for subgoal in subgoals if subgoal.subgoal_id == subgoal_id), None)
        updates = []
        values = []
        for key, value in fields.items():
            updates.append(f"{key} = ?")
            values.append(self._normalize_value(value))
        updates.append("updated_at = ?")
        values.append(datetime.utcnow().isoformat())
        values.extend([task_id, subgoal_id])
        with self._write_connection() as connection:
            cursor = connection.execute(
                f"UPDATE task_subgoals SET {', '.join(updates)} WHERE task_id = ? AND subgoal_id = ?",
                values,
            )
            if cursor.rowcount == 0:
                return None
        return next((subgoal for subgoal in self.list_subgoals(task_id) if subgoal.subgoal_id == subgoal_id), None)

    def list_memory(self, task_id: str | None = None) -> list[MemoryRecord]:
        query = "SELECT * FROM task_memory"
        params: tuple[object, ...] = ()
        if task_id is not None:
            query += " WHERE task_id = ?"
            params = (task_id,)
        query += " ORDER BY created_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
            return [row_to_memory(dict(row)) for row in rows]

    def search_memory(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        terms = _tokenize(query)
        if not terms:
            return []
        scored: list[tuple[float, MemoryRecord]] = []
        for memory in self.list_memory():
            score = _score_memory(memory, terms)
            if score > 0:
                scored.append((score, memory))
        scored.sort(key=lambda item: (-item[0], item[1].created_at, item[1].id or 0))
        return [memory for _, memory in scored[:limit]]

    def list_snapshots(self, task_id: str) -> list[SnapshotRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_snapshots WHERE task_id = ? ORDER BY created_at DESC",
                (task_id,),
            ).fetchall()
            return [row_to_snapshot(dict(row)) for row in rows]

    def get_latest_snapshot(self, task_id: str) -> SnapshotRecord | None:
        snapshots = self.list_snapshots(task_id)
        return snapshots[0] if snapshots else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _write_connection(self):
        self._lock.acquire()
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
            self._lock.release()

    def _normalize_value(self, value):
        if value is None:
            return None
        if isinstance(value, (TaskStatus, SubgoalStatus)):
            return value.value
        if hasattr(value, "model_dump"):
            return encode_json(value)
        if isinstance(value, (list, dict)):
            return encode_json(value)
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def _ensure_columns(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("PRAGMA table_info(tasks)").fetchall()
        columns = {row[1] for row in rows}
        if "latest_retrieval" not in columns and columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN latest_retrieval TEXT")


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in text.replace("/", " ").replace("-", " ").split():
        cleaned = "".join(ch.lower() for ch in raw if ch.isalnum() or ch == "_")
        if len(cleaned) >= 2:
            tokens.append(cleaned)
    return tokens


def _score_memory(memory: MemoryRecord, terms: list[str]) -> float:
    sources = [
        (memory.title.lower(), 1.8),
        (memory.content.lower(), 1.0),
        (' '.join(memory.keywords).lower(), 1.6),
        (' '.join(memory.related_files).lower(), 1.4),
    ]
    score = 0.0
    matched_terms = 0
    for term in terms:
        hit = False
        for haystack, weight in sources:
            if term in haystack:
                score += weight
                hit = True
        if hit:
            matched_terms += 1

    if terms:
        score += (matched_terms / len(terms)) * 2.0

    title_tokens = set(_tokenize(memory.title))
    keyword_tokens = set(_tokenize(' '.join(memory.keywords)))
    file_tokens = set(_tokenize(' '.join(memory.related_files)))
    content_tokens = set(_tokenize(memory.content[:500]))
    exact_hits = 0.0
    for term in terms:
        if term in title_tokens:
            exact_hits += 2.0
        elif term in keyword_tokens:
            exact_hits += 1.0
        elif term in file_tokens:
            exact_hits += 1.0
        elif term in content_tokens:
            exact_hits += 0.5
    score += exact_hits * 0.4

    kind = memory.kind.lower()
    if kind == 'run_summary':
        score += 0.4
    elif kind == 'task_plan':
        score += 0.8
    elif kind == 'lesson_success':
        score += 1.2
    elif kind == 'lesson_failure':
        score += 1.4

    age_days = max((datetime.utcnow() - memory.created_at).days, 0)
    score += max(0.0, 0.8 - (age_days * 0.03))
    return score
