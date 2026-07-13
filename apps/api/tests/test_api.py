from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from apps.api.main import app
from assistant_tools import SNAPSHOT_MANIFEST_NAME, run_command

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "app.db"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
WORKSPACE_FILE = DATA_DIR / "demo_workspace" / "src" / "demo_app" / "formatter.py"
BUGGY_SOURCE = '''from __future__ import annotations


def normalize_username(raw: str) -> str:
    """Normalize a display name for consistent comparisons."""
    return raw.strip().upper()
'''


def reset_state() -> None:
    _clear_database(DB_PATH)
    _rmtree_with_retry(SNAPSHOT_DIR)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_FILE.write_text(BUGGY_SOURCE, encoding="utf-8")


def _clear_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS task_artifacts;
            DROP TABLE IF EXISTS task_snapshots;
            DROP TABLE IF EXISTS task_events;
            DROP TABLE IF EXISTS task_memory;
            DROP TABLE IF EXISTS tasks;
            """
        )
        connection.commit()


def _rmtree_with_retry(path: Path, attempts: int = 30) -> None:
    for index in range(attempts):
        try:
            if path.exists():
                shutil.rmtree(path)
            return
        except PermissionError:
            if index == attempts - 1:
                raise
            time.sleep(0.1)


def wait_for_terminal(client: TestClient, task_id: str, timeout_seconds: float = 10.0):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/tasks/{task_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "rolled_back", "cancelled"}:
            return payload
        time.sleep(0.1)
    raise AssertionError("Task did not reach a terminal state in time")


def test_task_run_patch_and_rollback() -> None:
    reset_state()

    with TestClient(app) as client:
        created = client.post("/tasks", json={"title": "Fix the demo bug"})
        assert created.status_code == 201
        task = created.json()
        task_id = task["id"]

        run_response = client.post(f"/tasks/{task_id}/run")
        assert run_response.status_code == 202
        assert run_response.json()["accepted"] is True

        final_task = wait_for_terminal(client, task_id)
        assert final_task["status"] == "succeeded"
        assert ".lower()" in (final_task["latest_diff"] or "")
        assert final_task["latest_test_result"]["passed"] is True
        assert final_task["latest_retrieval"]
        assert final_task["memory"]

        detail = client.get(f"/tasks/{task_id}").json()
        event_types = [event["type"] for event in detail["events"]]
        branch_created_events = [event for event in detail["events"] if event["type"] == "branch.created"]
        branch_selected_events = [event for event in detail["events"] if event["type"] == "branch.selected"]
        assert len(branch_created_events) >= 3
        assert len(branch_selected_events) >= 1
        assert branch_selected_events[0]["payload"]["candidate_count"] >= 3
        assert any(candidate.get("selected") for candidate in branch_selected_events[0]["payload"]["candidates"])
        assert "agent.planner.completed" in event_types
        assert "agent.executor.started" in event_types
        assert "agent.executor.completed" in event_types
        assert "agent.reviewer.started" in event_types
        assert "agent.reviewer.completed" in event_types
        assert "patch.applied" in event_types
        assert "task.succeeded" in event_types
        assert "command.started" in event_types
        assert "command.completed" in event_types
        assert "llm.plan.skipped" in event_types
        assert "react.observation" in event_types
        assert detail["events"][-1]["payload"]["review_decision"] == "approve"

        rollback_response = client.post(f"/tasks/{task_id}/rollback")
        assert rollback_response.status_code == 200
        assert rollback_response.json()["task"]["status"] == "rolled_back"

        restored_source = WORKSPACE_FILE.read_text(encoding="utf-8")
        assert ".upper()" in restored_source

        second = client.post("/tasks", json={"title": "Fix the demo bug"})
        assert second.status_code == 201
        second_task_id = second.json()["id"]
        second_run = client.post(f"/tasks/{second_task_id}/run")
        assert second_run.status_code == 202
        assert second_run.json()["accepted"] is True

        second_final = wait_for_terminal(client, second_task_id)
        assert second_final["status"] == "succeeded"
        assert second_final["latest_retrieval"]

        second_detail = client.get(f"/tasks/{second_task_id}").json()
        second_event_types = [event["type"] for event in second_detail["events"]]
        assert "memory.loaded" in second_event_types
        memory_events = [event for event in second_detail["events"] if event["type"] == "memory.loaded"]
        assert memory_events and len(memory_events[0]["payload"]["matches"]) >= 1
        assert second_detail["memory"]

        policy = client.get("/security/policy")
        assert policy.status_code == 200
        policy_payload = policy.json()
        assert policy_payload["snapshot_integrity_required"] is True
        assert any("pytest" in rule["args_prefix"] for rule in policy_payload["allowed_commands"])

        artifacts = client.get(f"/tasks/{task_id}/artifacts")
        assert artifacts.status_code == 200
        artifact_rows = artifacts.json()
        artifact_types = [artifact["type"] for artifact in artifact_rows]
        assert "diff" in artifact_types
        assert "test_report" in artifact_types
        assert any(".lower()" in artifact["content"] for artifact in artifact_rows if artifact["type"] == "diff")

        with client.stream("GET", f"/tasks/{task_id}/events") as stream:
            assert stream.status_code == 200
            first_chunk = next(stream.iter_text())
            assert "event:" in first_chunk
            assert "task.started" in first_chunk or "file.read" in first_chunk
            stream.close()


def test_command_isolation_blocks_unapproved_commands() -> None:
    with pytest.raises(PermissionError):
        run_command(
            ["cmd.exe", "/c", "echo", "hello"],
            workspace_root=DATA_DIR / "demo_workspace",
            allowed_commands=[],
        )


def test_rollbacks_reject_tampered_snapshots() -> None:
    reset_state()

    with TestClient(app) as client:
        created = client.post("/tasks", json={"title": "Fix the demo bug"})
        task_id = created.json()["id"]
        client.post(f"/tasks/{task_id}/run")

        final_task = wait_for_terminal(client, task_id)
        assert final_task["status"] == "succeeded"

        detail = client.get(f"/tasks/{task_id}").json()
        snapshot_path = Path(detail["snapshots"][0]["path"])
        manifest_path = snapshot_path / SNAPSHOT_MANIFEST_NAME
        manifest_path.write_text("{}", encoding="utf-8")

        rollback_response = client.post(f"/tasks/{task_id}/rollback")
        assert rollback_response.status_code == 400
        assert "workspace" in rollback_response.text
