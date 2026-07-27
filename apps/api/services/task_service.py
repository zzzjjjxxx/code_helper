from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from assistant_shared.models import SubgoalStatus, TaskChatResponse, TaskCreateRequest, TaskDetail, TaskRunResponse, TaskStatus, TaskStep
from agent_core import DemoWorkflow, advance_status
from agent_core.llm import OpenAIPlanner

from apps.api.core.config import Settings
from apps.api.services.event_service import EventService
from apps.api.services.rollback_service import RollbackService
from apps.api.storage.sqlite import SQLiteStore


ACTIVE_STATUSES = {
    TaskStatus.queued,
    TaskStatus.reading,
    TaskStatus.analyzing,
    TaskStatus.patching,
    TaskStatus.testing,
}


class TaskService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: SQLiteStore,
        events: EventService,
        rollback_service: RollbackService,
    ) -> None:
        self.settings = settings
        self.store = store
        self.events = events
        self.rollback_service = rollback_service
        self.chat_planner = OpenAIPlanner(asdict(self.settings.llm_config))
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_follow_up_tasks: dict[str, asyncio.Task[None]] = {}

    def create_task(self, request: TaskCreateRequest) -> TaskDetail:
        # DOC_ANCHOR: task_service.create_task
        repository_path = Path(request.repository_path or self.settings.workspace_root)
        if not repository_path.is_absolute():
            repository_path = (self.settings.root_dir / repository_path).resolve()
        focus_paths = [path.strip() for path in request.focus_paths if path.strip()] or ['src/demo_app/formatter.py']
        created = self.store.create_task(
            title=request.title,
            description=request.description,
            repository_path=str(repository_path),
            focus_paths=focus_paths,
        )
        return self.store.get_task_detail(created.id)

    def list_tasks(self) -> list[TaskDetail]:
        return self.store.list_tasks()

    def get_task(self, task_id: str) -> TaskDetail:
        return self.store.get_task_detail(task_id)

    async def run_task(self, task_id: str) -> TaskRunResponse:
        task = await asyncio.to_thread(self.store.get_task, task_id)
        if task.status in ACTIVE_STATUSES and (task_id in self._running_tasks or task_id in self._pending_follow_up_tasks):
            return TaskRunResponse(task=task, accepted=False)

        await self._transition(task_id, TaskStatus.queued, current_step=TaskStep.read.value)
        running = asyncio.create_task(self._execute(task_id))
        self._running_tasks[task_id] = running
        running.add_done_callback(lambda _task: self._running_tasks.pop(task_id, None))
        return TaskRunResponse(task=await asyncio.to_thread(self.store.get_task, task_id), accepted=True)

    async def rollback(self, task_id: str):
        return await self.rollback_service.rollback(task_id)

    async def delete_task(self, task_id: str):
        task = await asyncio.to_thread(self.store.get_task, task_id)
        if task.status in ACTIVE_STATUSES or task_id in self._running_tasks or task_id in self._pending_follow_up_tasks:
            raise RuntimeError('Cannot delete an active conversation')

        deleted = await asyncio.to_thread(self.store.delete_task, task_id)
        snapshot_dir = Path(self.settings.snapshot_root) / task_id
        if snapshot_dir.exists():
            await asyncio.to_thread(shutil.rmtree, snapshot_dir, True)
        return deleted

    async def _execute(self, task_id: str, *, task_description_override: str | None = None) -> None:
        # DOC_ANCHOR: task_service.execute_loop
        task = await asyncio.to_thread(self.store.get_task_detail, task_id)
        task_description = task_description_override or task.description
        retrieval_query = self._build_retrieval_query(task.title, task_description, task.focus_paths)
        memory_matches = await asyncio.to_thread(self.store.search_memory, retrieval_query, 5)
        if memory_matches:
            await self.events.emit(
                task_id=task_id,
                event_type='memory.loaded',
                step=TaskStep.read,
                message=f'Loaded {len(memory_matches)} relevant memory record(s)',
                payload={
                    'query': retrieval_query,
                    'matches': [memory.model_dump(mode='json') for memory in memory_matches],
                },
            )
        await self._transition(task_id, TaskStatus.reading, current_step=TaskStep.read.value, started_at=True)
        workflow = DemoWorkflow(
            workspace_root=task.repository_path,
            focus_paths=task.focus_paths,
            snapshot_root=self.settings.snapshot_root,
            allowed_commands=self.settings.allowed_commands,
            llm_config=asdict(self.settings.llm_config),
            memory_notes=memory_matches,
        )

        async def emit(event_type: str, step: TaskStep | str, message: str, payload: dict | None = None) -> None:
            stored = await self.events.emit(
                task_id=task_id,
                event_type=event_type,
                step=step,
                message=message,
                payload=payload or {},
            )
            await self._apply_goal_event(task_id, stored.type, stored.payload)
            await self._apply_progress_event(task_id, stored.type, stored.step, stored.payload)

        try:
            result = await workflow.run(
                task_id=task_id,
                task_title=task.title,
                task_description=task_description,
                emit=emit,
            )
            if result.snapshot_path:
                await asyncio.to_thread(
                    self.store.create_snapshot,
                    task_id=task_id,
                    label='before_patch',
                    path=result.snapshot_path,
                )
            approved = result.final_test.passed and result.review_decision == 'approve'
            terminal_summary = self._build_terminal_summary(task.description, result)
            if result.final_test.passed and result.review_decision != 'approve':
                terminal_summary = f'{terminal_summary} Reviewer did not approve the final branch.'

            await asyncio.to_thread(
                self.store.update_task,
                task_id,
                latest_diff=result.patch.diff if result.patch else None,
                latest_test_result=result.final_test,
                latest_retrieval=result.retrieval_hits,
                summary=terminal_summary,
                last_error=None,
            )
            memory_entries = self._build_memory_entries(
                task_title=task.title,
                task_description=task.description,
                result=result,
            )
            for index, entry in enumerate(memory_entries, start=1):
                stored_memory = await asyncio.to_thread(
                    self.store.create_memory,
                    task_id=task_id,
                    kind=str(entry['kind']),
                    title=str(entry['title']),
                    content=str(entry['content']),
                    keywords=list(entry['keywords']),
                    related_files=list(entry['related_files']),
                )
                await self.events.emit(
                    task_id=task_id,
                    event_type='memory.created',
                    step=TaskStep.summarize,
                    message=f"Stored memory note {index}/{len(memory_entries)}: {entry['kind']}",
                    payload={'memory': stored_memory.model_dump(mode='json'), 'memory_kind': entry['kind']},
                )
            if result.patch:
                await asyncio.to_thread(
                    self.store.create_artifact,
                    task_id=task_id,
                    type='diff',
                    name=Path(result.patch.path).name,
                    content=result.patch.diff,
                )
                await self.events.emit(
                    task_id=task_id,
                    event_type='artifact.created',
                    step=TaskStep.patch,
                    message='Stored the workspace diff',
                    payload={'type': 'diff', 'name': Path(result.patch.path).name},
                )
            await asyncio.to_thread(
                self.store.create_artifact,
                task_id=task_id,
                type='test_report',
                name='final pytest result',
                content=json.dumps(result.final_test.model_dump(mode='json'), ensure_ascii=False),
            )
            await self.events.emit(
                task_id=task_id,
                event_type='artifact.created',
                step=TaskStep.test,
                message='Stored the final test report',
                payload={'type': 'test_report', 'name': 'final pytest result'},
            )
            if result.branch_comparison:
                # DOC_ANCHOR: task_service.branch_comparison_artifact
                comparison_artifact = await asyncio.to_thread(
                    self.store.create_artifact,
                    task_id=task_id,
                    type='branch_comparison',
                    name='branch comparison summary',
                    content=json.dumps(result.branch_comparison, ensure_ascii=False, indent=2),
                )
                await self.events.emit(
                    task_id=task_id,
                    event_type='artifact.created',
                    step=TaskStep.summarize,
                    message='Stored the branch comparison summary',
                    payload={'type': 'branch_comparison', 'name': comparison_artifact.name},
                )
            if approved:
                terminal_status = TaskStatus.succeeded
                last_error = None
            else:
                terminal_status = TaskStatus.failed
                if result.final_test.passed:
                    last_error = 'Reviewer did not approve the final branch'
                else:
                    last_error = 'Tests failed after applying the patch'
            await self._transition(
                task_id,
                terminal_status,
                current_step=TaskStep.summarize.value,
                finished_at=True,
                summary=terminal_summary,
                last_error=last_error,
            )
            await self.events.emit(
                task_id=task_id,
                event_type='task.succeeded' if approved else 'task.failed',
                step=TaskStep.summarize,
                message=terminal_summary,
                payload={
                    'changed_files': result.changed_files,
                    'final_test_passed': result.final_test.passed,
                    'snapshot_path': result.snapshot_path,
                    'plan_source': result.plan_source,
                    'llm_used': result.llm_used,
                    'llm_model': result.llm_model,
                    'branch_count': result.branch_count,
                    'review_decision': result.review_decision,
                },
            )
        except Exception as exc:  # pragma: no cover - surfaced in task status and tests
            await self._transition(
                task_id,
                TaskStatus.failed,
                current_step=TaskStep.summarize.value,
                finished_at=True,
                last_error=str(exc),
                summary='The workflow crashed before finishing.',
            )
            await self.events.emit(
                task_id=task_id,
                event_type='task.failed',
                step=TaskStep.summarize,
                message='The workflow crashed before finishing.',
                payload={'error': str(exc)},
            )

    async def chat_task(self, task_id: str, message: str) -> TaskChatResponse:
        task = await asyncio.to_thread(self.store.get_task_detail, task_id)
        user_event = await self.events.emit(
            task_id=task_id,
            event_type='task.chat.request',
            step=TaskStep.analyze,
            message=message,
            payload={
                'message': message,
                'task_status': task.status.value,
                'current_step': task.current_step,
            },
        )
        chat_context = await asyncio.to_thread(self._build_chat_context, task_id, task, message, user_event)
        response = await asyncio.to_thread(
            self.chat_planner.chat_task,
            context=chat_context,
            user_message=message,
        )

        implementation_request = response.implementation_request
        reply = response.reply
        suggested_panel = response.suggested_panel or 'summary'
        follow_up_started = False

        review = await asyncio.to_thread(
            self.chat_planner.review_chat_response,
            context=chat_context,
            user_message=message,
            assistant_reply=reply,
            implementation_request=implementation_request,
        )
        if review.suggested_panel:
            suggested_panel = review.suggested_panel
        if not review.adequate and review.corrected_reply:
            reply = review.corrected_reply

        if implementation_request:
            follow_up_description = self._build_follow_up_description(task.description, message)
            follow_up_started = await self._start_follow_up_execution(
                task_id,
                follow_up_description=follow_up_description,
                wait_for_active_run=task.status in ACTIVE_STATUSES or task_id in self._running_tasks,
            )
            if not reply:
                reply = 'I have queued the implementation and will apply it in the workspace.'

        await self.events.emit(
            task_id=task_id,
            event_type='task.chat.response',
            step=TaskStep.analyze,
            message=reply,
            payload={
                'reply': reply,
                'suggested_panel': suggested_panel,
                'used_llm': response.provider != 'heuristic',
                'model': response.model,
                'implementation_request': implementation_request,
                'follow_up_started': follow_up_started,
                'reply_review_adequate': review.adequate,
                'reply_review_reason': review.reason,
            },
        )
        return TaskChatResponse(
            reply=reply,
            suggested_panel=suggested_panel,
            used_llm=response.provider != 'heuristic',
            model=response.model,
            implementation_request=implementation_request,
            follow_up_started=follow_up_started,
        )

    async def _start_follow_up_execution(
        self,
        task_id: str,
        *,
        follow_up_description: str,
        wait_for_active_run: bool,
    ) -> bool:
        async def run_follow_up() -> None:
            current = self._running_tasks.get(task_id)
            if wait_for_active_run and current is not None:
                try:
                    await current
                except Exception:
                    pass
            await self._transition(task_id, TaskStatus.queued, current_step=TaskStep.read.value)
            running = asyncio.create_task(
                self._execute(task_id, task_description_override=follow_up_description)
            )
            self._running_tasks[task_id] = running
            running.add_done_callback(lambda _task: self._running_tasks.pop(task_id, None))

        if wait_for_active_run and task_id in self._running_tasks:
            pending = asyncio.create_task(run_follow_up())
            self._pending_follow_up_tasks[task_id] = pending
            pending.add_done_callback(lambda _task: self._pending_follow_up_tasks.pop(task_id, None))
            return True

        await self._transition(task_id, TaskStatus.queued, current_step=TaskStep.read.value)
        running = asyncio.create_task(
            self._execute(task_id, task_description_override=follow_up_description)
        )
        self._running_tasks[task_id] = running
        running.add_done_callback(lambda _task: self._running_tasks.pop(task_id, None))
        return True

    def _build_follow_up_description(self, original_description: str, message: str) -> str:
        return (
            'User requested an implementation change through chat.\n'
            f'Instruction: {message.strip()}\n'
            'Make the smallest safe code change needed to satisfy the instruction.\n'
            'If the task requires a brand new file, create it in the workspace.\n'
            'If an existing file should be updated, modify it directly.\n\n'
            f'Original task context:\n{original_description}'
        )

    def _build_chat_context(
        self,
        task_id: str,
        task: TaskDetail,
        message: str,
        user_event,
    ) -> dict[str, object]:
        artifacts = self.store.list_artifacts(task_id)
        recent_events = [event.model_dump(mode='json') for event in task.events[-16:]]
        recent_events.append(user_event.model_dump(mode='json'))
        return {
            'task': {
                'id': task.id,
                'title': task.title,
                'description': task.description,
                'repository_path': task.repository_path,
                'status': task.status.value,
                'current_step': task.current_step,
                'focus_paths': task.focus_paths,
            },
            'user_message': message,
            'latest_diff': task.latest_diff,
            'latest_test': task.latest_test_result.model_dump(mode='json') if task.latest_test_result else None,
            'retrieval_hits': [hit.model_dump(mode='json') for hit in task.latest_retrieval[:6]],
            'memory_notes': [note.model_dump(mode='json') for note in task.memory[:6]],
            'subgoals': [subgoal.model_dump(mode='json') for subgoal in task.subgoals[:8]],
            'recent_events': recent_events,
            'artifacts': [artifact.model_dump(mode='json') for artifact in artifacts[-8:]],
        }


    def list_artifacts(self, task_id: str):
        return self.store.list_artifacts(task_id)

    async def _transition(
        # DOC_ANCHOR: task_service.transition
        self,
        task_id: str,
        next_status: TaskStatus,
        *,
        current_step: str | None = None,
        started_at: bool = False,
        finished_at: bool = False,
        summary: str | None = None,
        last_error: str | None = None,
    ) -> None:
        task = await asyncio.to_thread(self.store.get_task, task_id)
        if task.status == next_status:
            updates: dict[str, object] = {
                'current_step': current_step,
                'summary': summary,
                'last_error': last_error,
            }
            now = datetime.utcnow().isoformat()
            if started_at:
                updates['started_at'] = now
            if finished_at:
                updates['finished_at'] = now
            await asyncio.to_thread(self.store.update_task, task_id, **updates)
            return
        advance_status(task.status, next_status)
        updates: dict[str, object] = {
            'status': next_status,
            'current_step': current_step,
            'summary': summary,
            'last_error': last_error,
        }
        now = datetime.utcnow().isoformat()
        if started_at:
            updates['started_at'] = now
        if finished_at:
            updates['finished_at'] = now
        await asyncio.to_thread(self.store.update_task, task_id, **updates)

    async def _apply_progress_event(self, task_id: str, event_type: str, step: str | None, payload: dict) -> None:
        # DOC_ANCHOR: task_service.progress_mapper
        status: TaskStatus | None = None
        current_step = step

        if event_type in {'task.started', 'retrieval.started', 'file.read'}:
            status = TaskStatus.reading
        elif event_type in {'branch.created', 'branch.selected', 'llm.plan.started', 'llm.plan.completed', 'agent.planner.started', 'agent.planner.completed', 'replan.requested', 'goal.planned', 'goal.started', 'goal.completed', 'goal.blocked', 'goal.skipped'}:
            status = TaskStatus.analyzing
        elif event_type in {'agent.executor.started', 'snapshot.created', 'patch.applied'}:
            status = TaskStatus.patching
        elif event_type in {'command.started', 'command.completed', 'test.completed', 'agent.executor.completed'}:
            if step == TaskStep.test.value:
                status = TaskStatus.testing
            elif step == TaskStep.review.value:
                status = TaskStatus.awaiting_review
            elif step == TaskStep.analyze.value:
                status = TaskStatus.analyzing
            else:
                status = TaskStatus.patching
        elif event_type in {'agent.reviewer.started', 'agent.reviewer.completed', 'task.summarized'}:
            status = TaskStatus.awaiting_review
        elif event_type in {'branch.reverted'}:
            status = TaskStatus.analyzing
        elif event_type == 'task.succeeded':
            status = TaskStatus.succeeded
        elif event_type == 'task.failed':
            status = TaskStatus.failed
        elif event_type == 'rollback.completed':
            status = TaskStatus.rolled_back

        if status is None:
            return

        await self._transition(task_id, status, current_step=current_step)

    async def _apply_goal_event(self, task_id: str, event_type: str, payload: dict) -> None:
        if event_type == 'goal.planned':
            subgoals = payload.get('subgoals', []) if isinstance(payload, dict) else []
            if isinstance(subgoals, list):
                await asyncio.to_thread(self.store.replace_subgoals, task_id, subgoals)
            return

        subgoal_payload = payload.get('subgoal') if isinstance(payload, dict) else None
        if not isinstance(subgoal_payload, dict):
            return
        subgoal_id = str(subgoal_payload.get('subgoal_id', '')).strip()
        if not subgoal_id:
            return

        updates: dict[str, object] = {}
        if event_type == 'goal.started':
            updates['status'] = SubgoalStatus.active
        elif event_type == 'goal.completed':
            updates['status'] = SubgoalStatus.completed
            updates['completed_at'] = datetime.utcnow()
        elif event_type == 'goal.blocked':
            updates['status'] = SubgoalStatus.blocked
        elif event_type == 'goal.skipped':
            updates['status'] = SubgoalStatus.skipped
        else:
            return

        if 'title' in subgoal_payload:
            updates['title'] = str(subgoal_payload.get('title', '')).strip()
        if 'description' in subgoal_payload:
            updates['description'] = str(subgoal_payload.get('description', '')).strip()
        if 'rationale' in subgoal_payload:
            updates['rationale'] = str(subgoal_payload.get('rationale', '')).strip()
        if 'phase' in subgoal_payload:
            updates['phase'] = str(subgoal_payload.get('phase', '')).strip()
        if 'success_criteria' in subgoal_payload:
            updates['success_criteria'] = list(subgoal_payload.get('success_criteria') or [])
        if 'files_to_read' in subgoal_payload:
            updates['files_to_read'] = list(subgoal_payload.get('files_to_read') or [])

        await asyncio.to_thread(self.store.update_subgoal, task_id, subgoal_id, **updates)

    def _build_retrieval_query(self, title: str, description: str, focus_paths: list[str]) -> str:
        parts = [title.strip(), description.strip(), ' '.join(focus_paths)]
        return ' '.join(part for part in parts if part)

    def _build_memory_keywords(self, title: str, description: str, changed_files: list[str]) -> list[str]:
        keywords = self._tokenize(title) + self._tokenize(description)
        for path in changed_files:
            keywords.extend(self._tokenize(Path(path).stem))
            keywords.extend(self._tokenize(Path(path).parent.as_posix()))
        return self._dedupe(keywords)

    def _build_memory_entries(
        self,
        *,
        task_title: str,
        task_description: str,
        result,
    ) -> list[dict[str, object]]:
        base_keywords = self._dedupe(
            self._tokenize(task_title)
            + self._tokenize(task_description)
            + self._tokenize(result.plan_source)
            + self._tokenize(result.review_decision)
        )
        changed_files = list(result.changed_files)
        outcome = 'passed' if result.final_test.passed else 'failed'
        summary_content = self._build_memory_content(
            result.summary,
            result.final_test.passed,
            result.changed_files,
            result.plan_source,
            result.review_decision,
            result.branch_count,
            result.subgoal_count,
        )
        return [
            {
                'kind': 'run_summary',
                'title': task_title,
                'content': summary_content,
                'keywords': self._dedupe(base_keywords + ['summary', outcome, 'run']),
                'related_files': changed_files,
            },
            {
                'kind': 'lesson_success' if result.final_test.passed and result.review_decision == 'approve' else 'lesson_failure',
                'title': f'{task_title} - {outcome} lesson',
                'content': self._build_lesson_content(result, changed_files),
                'keywords': self._dedupe(base_keywords + ['lesson', outcome, 'pattern']),
                'related_files': changed_files,
            },
            {
                'kind': 'task_plan',
                'title': f'{task_title} - task plan',
                'content': self._build_plan_memory_content(result),
                'keywords': self._dedupe(base_keywords + ['plan', 'subgoal', 'workflow']),
                'related_files': changed_files,
            },
        ]

    def _build_memory_content(
        self,
        summary: str,
        passed: bool,
        changed_files: list[str],
        plan_source: str,
        review_decision: str,
        branch_count: int,
        subgoal_count: int,
    ) -> str:
        status = 'passed' if passed else 'failed'
        lines = [
            summary,
            f'Verification status: {status}',
            f'Plan source: {plan_source}',
            f'Review decision: {review_decision}',
            f'Branches explored: {branch_count}',
            f'Subgoals planned: {subgoal_count}',
        ]
        if changed_files:
            lines.append('Changed files: ' + ', '.join(changed_files))
        return '\n'.join(line for line in lines if line)

    def _build_terminal_summary(self, task_description: str, result) -> str:
        summary = (result.summary or '').strip()
        if not result.changed_files:
            summary = re.sub(
                r'(?i)(?:^|\s)(?:created or changed files|changed files)\s*:\s*(?:none|no files?|empty)\.?',
                ' ',
                summary,
            ).strip()
        if re.match(r'^(approved|reviewer approved)\b', summary, flags=re.IGNORECASE):
            summary = 'Implementation completed.'
        patch_content = result.patch.after if result.patch else ''
        request_lower = task_description.lower()
        summary_parts = [summary] if summary else []
        if result.changed_files and not any(path in summary for path in result.changed_files):
            summary_parts.append('Created or changed files: ' + ', '.join(result.changed_files))
        requested_complexity = any(keyword in request_lower for keyword in ('time complexity', 'space complexity', 'complexity', '\u65f6\u95f4\u590d\u6742\u5ea6', '\u7a7a\u95f4\u590d\u6742\u5ea6', '\u590d\u6742\u5ea6'))
        if not requested_complexity:
            return ' '.join(summary_parts)
        summary_lower = summary.lower()
        has_time = 'time complexity' in summary_lower or '\u65f6\u95f4\u590d\u6742\u5ea6' in summary
        has_space = 'space complexity' in summary_lower or '\u7a7a\u95f4\u590d\u6742\u5ea6' in summary
        if has_time and has_space:
            return ' '.join(summary_parts)

        algorithm = 'quicksort' if 'quicksort' in patch_content.lower() else 'the implemented algorithm'
        time_complexity = self._extract_complexity_line(patch_content, ('time complexity', '\u65f6\u95f4\u590d\u6742\u5ea6'))
        space_complexity = self._extract_complexity_line(patch_content, ('space complexity', '\u7a7a\u95f4\u590d\u6742\u5ea6'))
        if not time_complexity and algorithm == 'quicksort':
            time_complexity = 'average O(n log n), worst O(n^2)'
        if not space_complexity and algorithm == 'quicksort':
            space_complexity = 'O(n) for recursive partitions'
        if not time_complexity:
            time_complexity = 'not documented in the generated file'
        if not space_complexity:
            space_complexity = 'not documented in the generated file'

        details = [f'Algorithm: {algorithm}.']
        if time_complexity:
            details.append(f'Time complexity: {time_complexity}.')
        if space_complexity:
            details.append(f'Space complexity: {space_complexity}.')
        return ' '.join(summary_parts + [' '.join(details)])

    def _extract_complexity_line(self, content: str, labels: tuple[str, ...]) -> str | None:
        for line in content.splitlines():
            normalized = line.strip().lstrip('#/*').strip()
            lowered = normalized.lower()
            if any(label in lowered for label in labels):
                value = re.split(r'[:\uFF1A]', normalized, maxsplit=1)
                if len(value) == 2 and value[1].strip():
                    return value[1].strip().rstrip('.')
        return None
    def _build_lesson_content(self, result, changed_files: list[str]) -> str:
        outcome = 'passed' if result.final_test.passed else 'failed'
        lines = [
            f'Outcome: {outcome}',
            f'Plan source: {result.plan_source}',
            f'Review decision: {result.review_decision}',
            f'Branches explored: {result.branch_count}',
            f'Subgoals planned: {result.subgoal_count}',
        ]
        if result.final_test.passed:
            lines.append('Lesson: this fix path is safe to reuse when the same files and tests show up again.')
        else:
            lines.append('Lesson: expand context and verify the tests before retrying the same patch path.')
        if changed_files:
            lines.append('Related files: ' + ', '.join(changed_files))
        return '\n'.join(lines)

    def _build_plan_memory_content(self, result) -> str:
        lines = [
            f'Plan source: {result.plan_source}',
            f'Review decision: {result.review_decision}',
            f'React turns: {result.react_turns}',
            f'Branches explored: {result.branch_count}',
            f'Subgoals planned: {result.subgoal_count}',
            f'Verification: {"passed" if result.final_test.passed else "failed"}',
        ]
        if result.changed_files:
            lines.append('Files touched: ' + ', '.join(result.changed_files))
        return '\n'.join(lines)

    def _tokenize(self, text: str) -> list[str]:
        tokens: list[str] = []
        for raw in text.replace('/', ' ').replace('-', ' ').split():
            cleaned = ''.join(ch.lower() for ch in raw if ch.isalnum() or ch == '_')
            if len(cleaned) >= 2:
                tokens.append(cleaned)
        return tokens

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered
