from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from apps.api.main import app
from assistant_tools import SNAPSHOT_MANIFEST_NAME, run_command
from agent_core.llm import ChatResponseResult, ChatResponseReviewResult

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
    for extra_generated in [WORKSPACE_FILE.parent / "sorter.py", WORKSPACE_FILE.parent.parent / "sorter.py"]:
        if extra_generated.exists():
            extra_generated.unlink()
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
        assert len(final_task["memory"]) >= 3
        assert {memory["kind"] for memory in final_task["memory"]} >= {
            "run_summary",
            "task_plan",
            "lesson_success",
        }

        detail = client.get(f"/tasks/{task_id}").json()
        event_types = [event["type"] for event in detail["events"]]
        branch_created_events = [event for event in detail["events"] if event["type"] == "branch.created"]
        branch_selected_events = [event for event in detail["events"] if event["type"] == "branch.selected"]
        goal_planned_events = [event for event in detail["events"] if event["type"] == "goal.planned"]
        goal_completed_events = [event for event in detail["events"] if event["type"] == "goal.completed"]
        comparison_events = [event for event in detail["events"] if event["type"] == "branch.comparison.completed"]
        assert len(branch_created_events) >= 5
        assert len(branch_selected_events) >= 1
        assert len(goal_planned_events) == 1
        assert len(goal_completed_events) >= 3
        assert branch_selected_events[0]["payload"]["candidate_count"] >= 5
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
        assert "goal.planned" in event_types
        assert "goal.completed" in event_types
        assert "react.observation" in event_types
        assert "branch.comparison.completed" in event_types
        assert comparison_events and comparison_events[0]["payload"]["candidate_count"] >= 5
        assert comparison_events[0]["payload"]["turn_count"] >= 1
        assert detail["events"][-1]["payload"]["review_decision"] == "approve"
        assert len(detail["subgoals"]) == 3
        assert [subgoal["phase"] for subgoal in detail["subgoals"]] == ["inspect", "implement", "verify"]
        assert all(subgoal["status"] == "completed" for subgoal in detail["subgoals"])

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
        assert len(second_final["memory"]) >= 3

        second_detail = client.get(f"/tasks/{second_task_id}").json()
        second_event_types = [event["type"] for event in second_detail["events"]]
        assert "memory.loaded" in second_event_types
        memory_events = [event for event in second_detail["events"] if event["type"] == "memory.loaded"]
        assert memory_events and len(memory_events[0]["payload"]["matches"]) >= 1
        retrieval_events = [event for event in second_detail["events"] if event["type"] == "retrieval.completed"]
        assert retrieval_events and retrieval_events[0]["payload"]["memory_note_count"] >= 1
        assert retrieval_events[0]["payload"]["memory_hints"]

        assert second_detail["memory"]
        assert {memory["kind"] for memory in second_detail["memory"]} >= {
            "run_summary",
            "task_plan",
        }

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
        assert "branch_comparison" in artifact_types
        assert any(".lower()" in artifact["content"] for artifact in artifact_rows if artifact["type"] == "diff")

        with client.stream("GET", f"/tasks/{task_id}/events") as stream:
            assert stream.status_code == 200
            first_chunk = next(stream.iter_text())
            assert "event:" in first_chunk
            assert "task.started" in first_chunk or "file.read" in first_chunk
            stream.close()


def test_rollback_without_snapshot_records_failure() -> None:
    reset_state()

    with TestClient(app) as client:
        created = client.post("/tasks", json={"title": "Rollback without snapshot"})
        task_id = created.json()["id"]

        rollback_response = client.post(f"/tasks/{task_id}/rollback")
        assert rollback_response.status_code == 400
        assert "No snapshot" in rollback_response.text

        detail = client.get(f"/tasks/{task_id}").json()
        assert detail["status"] == "created"
        assert detail["current_step"] == "rollback"
        assert detail["last_error"] == "No snapshot is available for rollback"
        assert any(event["type"] == "rollback.failed" for event in detail["events"])




def test_chat_implementation_request_starts_follow_up_execution() -> None:
    reset_state()

    with TestClient(app) as client:
        created = client.post("/tasks", json={"title": "Create a sorting file"})
        assert created.status_code == 201
        task_id = created.json()["id"]

        service = app.state.container.task_service
        captured: dict[str, object] = {}
        original_chat_task = service.chat_planner.chat_task
        original_review_chat_response = service.chat_planner.review_chat_response
        original_start_follow_up_execution = service._start_follow_up_execution

        async def fake_start_follow_up_execution(task_id: str, *, follow_up_description: str, wait_for_active_run: bool) -> bool:
            captured["task_id"] = task_id
            captured["follow_up_description"] = follow_up_description
            captured["wait_for_active_run"] = wait_for_active_run
            return True

        try:
            service.chat_planner.chat_task = lambda **_: ChatResponseResult(
                reply="I will implement it now.",
                suggested_panel="summary",
                implementation_request=True,
                model="fake-model",
                provider="heuristic",
            )
            service.chat_planner.review_chat_response = lambda **_: ChatResponseReviewResult(
                adequate=True,
                corrected_reply=None,
                reason="ok",
                suggested_panel=None,
                model="fake-model",
                provider="heuristic",
            )
            service._start_follow_up_execution = fake_start_follow_up_execution

            response = client.post(f"/tasks/{task_id}/chat", json={"message": "create a sorting file with quicksort"})
            assert response.status_code == 200

            body = response.json()
            assert body["implementation_request"] is True
            assert body["follow_up_started"] is True
            assert captured["task_id"] == task_id
            assert "create it in the workspace" in str(captured["follow_up_description"]).lower()
            assert "sorter.py" in str(captured["follow_up_description"])
            assert captured["wait_for_active_run"] is False
        finally:
            service.chat_planner.chat_task = original_chat_task
            service.chat_planner.review_chat_response = original_review_chat_response
            service._start_follow_up_execution = original_start_follow_up_execution

def test_chat_implementation_request_creates_new_file() -> None:
    reset_state()

    created_file = DATA_DIR / "demo_workspace" / "sorter.py"

    with TestClient(app) as client:
        created = client.post("/tasks", json={"title": "Create a sorting file"})
        assert created.status_code == 201
        task_id = created.json()["id"]

        service = app.state.container.task_service
        original_chat_task = service.chat_planner.chat_task
        original_review_chat_response = service.chat_planner.review_chat_response

        try:
            service.chat_planner.chat_task = lambda **_: ChatResponseResult(
                reply="I will create the sorter file now.",
                suggested_panel="summary",
                implementation_request=True,
                model="fake-model",
                provider="heuristic",
            )
            service.chat_planner.review_chat_response = lambda **_: ChatResponseReviewResult(
                adequate=True,
                corrected_reply=None,
                reason="ok",
                suggested_panel=None,
                model="fake-model",
                provider="heuristic",
            )

            response = client.post(
                f"/tasks/{task_id}/chat",
                json={"message": "create a sorting file with quicksort"},
            )
            assert response.status_code == 200
            assert response.json()["follow_up_started"] is True

            final_task = wait_for_terminal(client, task_id, timeout_seconds=45.0)
            assert final_task["status"] in {"succeeded", "failed"}
            assert created_file.exists()
            assert "quicksort" in created_file.read_text(encoding="utf-8").lower()
        finally:
            service.chat_planner.chat_task = original_chat_task
            service.chat_planner.review_chat_response = original_review_chat_response

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

        failed_detail = client.get(f"/tasks/{task_id}").json()
        assert failed_detail["status"] == "succeeded"
        assert failed_detail["last_error"]
        assert failed_detail["current_step"] == "rollback"
        assert any(event["type"] == "rollback.failed" for event in failed_detail["events"])
