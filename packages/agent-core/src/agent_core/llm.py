from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import uuid4

from .skills import skill_catalog
from .workflow.patch_model import PatchProposal


@dataclass(slots=True)
class LLMPlanResult:
    proposal: PatchProposal
    summary: str
    rationale: str
    model: str
    provider: str = 'openai'


@dataclass(slots=True)
class TaskSubgoalSpec:
    subgoal_id: str = field(default_factory=lambda: str(uuid4()))
    position: int = 0
    phase: str = 'analysis'
    title: str = ''
    description: str = ''
    success_criteria: list[str] = field(default_factory=list)
    files_to_read: list[str] = field(default_factory=list)
    rationale: str = ''


@dataclass(slots=True)
class TaskPlanResult:
    subgoals: list[TaskSubgoalSpec]
    summary: str
    model: str
    provider: str = 'openai'


@dataclass(slots=True)
class ReActDecision:
    action: str
    summary: str = ''
    rationale: str = ''
    files_to_read: list[str] = field(default_factory=list)
    proposal: PatchProposal | None = None
    model: str = ''
    provider: str = 'openai'


@dataclass(slots=True)
class ExecutorToolCall:
    tool: str
    path: str = ''
    old: str = ''
    content: str = ''
    summary: str = ''
    model: str = ''
    provider: str = 'openai'


@dataclass(slots=True)
class ReviewDecision:
    action: str
    summary: str = ''
    rationale: str = ''
    files_to_read: list[str] = field(default_factory=list)
    model: str = ''
    provider: str = 'openai'


@dataclass(slots=True)
class ChatResponseResult:
    reply: str
    suggested_panel: str | None = None
    intent: str = 'unknown'
    implementation_request: bool = False
    model: str = ''
    provider: str = 'openai'


@dataclass(slots=True)
class ChatResponseReviewResult:
    adequate: bool
    corrected_reply: str | None = None
    reason: str = ''
    suggested_panel: str | None = None
    model: str = ''
    provider: str = 'openai'


class OpenAIPlanner:
    def __init__(self, config: Mapping[str, Any] | None = None):
        data = dict(config or {})
        self.provider = str(data.get('provider', 'openai')).strip().lower()
        self.base_url = str(data.get('base_url', 'https://api.openai.com/v1')).strip().rstrip('/')
        self.api_key_env = str(data.get('api_key_env', 'OPENAI_API_KEY')).strip()
        self.model = str(data.get('model', '')).strip()
        self.temperature = float(data.get('temperature', 0.2))
        self.max_output_tokens = int(data.get('max_output_tokens', 2048))
        self.timeout_seconds = int(data.get('timeout_seconds', 120))

    @property
    def api_key(self) -> str:
        return os.getenv(self.api_key_env, '').strip()

    @property
    def enabled(self) -> bool:
        return self.provider in {'openai', 'deepseek'} and bool(self.api_key) and bool(self.model) and self.model != 'your-model-name'

    @property
    def disabled_reason(self) -> str:
        if self.provider not in {'openai', 'deepseek'}:
            return f'unsupported provider: {self.provider}'
        if not self.model or self.model == 'your-model-name':
            return 'missing model'
        if not self.api_key:
            return f'missing API key from {self.api_key_env}'
        return 'disabled'

    def propose_patch(
        self,
        *,
        task_title: str,
        task_description: str,
        retrieval_hits: list[dict[str, Any]],
        memory_notes: list[dict[str, Any]],
        file_contexts: list[dict[str, Any]],
        baseline_test: dict[str, Any],
    ) -> LLMPlanResult | None:
        if not self.enabled:
            return None

        prompt_payload = {
            'task_title': task_title,
            'task_description': task_description,
            'retrieval_hits': retrieval_hits[:6],
            'memory_notes': memory_notes[:6],
            'file_contexts': [
                {
                    'path': entry.get('path', ''),
                    'content': str(entry.get('content', ''))[:5000],
                }
                for entry in file_contexts[:8]
            ],
            'baseline_test': baseline_test,
            'instructions': (
                'Return one JSON object only. Choose the smallest safe patch. '
                'If you need to create a new file, set operation to create, old to an empty string, and new to the full file content. '
                'Otherwise set operation to modify. The JSON object must include operation, path, old, new, summary, and rationale.'
            ),
        }
        system_prompt = (
            'You are a code-repair planner. Study the task, retrieval hits, memory notes, and file context. '
            'Return one JSON object only; never use markdown or prose outside JSON. '
            'The object must contain operation, path, old, new, summary, and rationale. '
            'Use operation=create only for a genuinely new file: path must be workspace-relative, old must be exactly "", '
            'and new must contain the complete runnable file contents. Use operation=modify only for an existing file present '
            'in file_contexts: old must be an exact contiguous substring copied from that context, and new must be the complete '
            'replacement text. Never invent a path, omit required code, return a diff wrapper, or return a prose-only plan. '
            'If the complete safe patch cannot be produced from the supplied context, return no patch proposal. '
            'summary must be a user-facing completion summary that covers every explicit deliverable in task_description, including requested explanations such as time or space complexity. '
            'Do not use approval-only summaries such as Approved creation; state what was created or changed and the requested facts. Mention changed files only when the verified changed_files list is non-empty.'
        )
        raw_text = self._invoke_responses_api(
            instructions=system_prompt,
            input_text=json.dumps(prompt_payload, ensure_ascii=False, indent=2),
        )
        if not raw_text:
            return None

        payload = _extract_json_object(raw_text)
        if not isinstance(payload, dict):
            return None

        operation = str(payload.get('operation', 'modify')).strip().lower()
        if operation not in {'modify', 'create'}:
            operation = 'modify'
        path = str(payload.get('path', '')).strip()
        old = str(payload.get('old', ''))
        new = str(payload.get('new', ''))
        summary = str(payload.get('summary', '')).strip()
        rationale = str(payload.get('rationale', '')).strip()
        if not path or not new:
            return None
        if operation == 'modify' and not old:
            return None
        if operation == 'create' and old != '':
            return None

        return LLMPlanResult(
            proposal=PatchProposal(path=path, old=old, new=new, operation=operation),
            summary=summary or 'Patch verified successfully.',
            rationale=rationale,
            model=self.model,
            provider=self.provider,
        )

    def plan_task_subgoals(
        self,
        *,
        task_title: str,
        task_description: str,
        focus_paths: list[str],
        retrieval_hits: list[dict[str, Any]],
        memory_notes: list[dict[str, Any]],
        file_contexts: list[dict[str, Any]],
    ) -> TaskPlanResult:
        if self.enabled:
            prompt_payload = {
                'task_title': task_title,
                'task_description': task_description,
                'focus_paths': focus_paths,
                'retrieval_hits': retrieval_hits[:6],
                'memory_notes': memory_notes[:6],
                'file_contexts': [
                    {
                        'path': entry.get('path', ''),
                        'content': str(entry.get('content', ''))[:3000],
                    }
                    for entry in file_contexts[:8]
                ],
                'instructions': (
                    'Return one JSON object only. Break the task into exactly three subgoals. '
                    'Each subgoal must include phase, title, description, success_criteria, files_to_read, and rationale.'
                ),
            }
            system_prompt = (
                'You are a task decomposition planner for a code-repair agent. Return one JSON object only with keys summary and subgoals. '
                'subgoals must contain exactly three ordered items with phases inspect, implement, and verify. Each item must contain '
                'phase, title, description, success_criteria, files_to_read, and rationale. Make every item concrete and verifiable. '
                'Use repository evidence from focus_paths, retrieval_hits, and file_contexts. files_to_read may contain only paths supplied '
                'by the context; do not invent filenames or claim a file exists. The verify step must distinguish passing tests, failed '
                'tests, and no tests collected. Do not include hidden reasoning, markdown, or extra prose.'
            )
            raw_text = self._invoke_responses_api(
                instructions=system_prompt,
                input_text=json.dumps(prompt_payload, ensure_ascii=False, indent=2),
            )
            if raw_text:
                payload = _extract_json_object(raw_text)
                if isinstance(payload, dict):
                    subgoals = self._parse_subgoal_payload(payload.get('subgoals'))
                    if len(subgoals) == 3:
                        return TaskPlanResult(
                            subgoals=self._normalize_subgoal_positions(subgoals),
                            summary=str(payload.get('summary', '')).strip() or self._default_plan_summary(task_title),
                            model=self.model,
                            provider=self.provider,
                        )

        subgoals = self._build_heuristic_subgoals(task_title, task_description, focus_paths, retrieval_hits, file_contexts, memory_notes)
        return TaskPlanResult(
            subgoals=self._normalize_subgoal_positions(subgoals),
            summary=self._default_plan_summary(task_title),
            model=self.model or 'heuristic',
            provider='heuristic',
        )

    def plan_next_step(self, *, state: Mapping[str, Any]) -> ReActDecision | None:
        if not self.enabled:
            return None

        system_prompt = (
            'You are the planner in a ReAct code-repair loop. Choose exactly one action: read_more, patch, or finish. '
            'Return one JSON object only with keys action, summary, rationale, files_to_read, and patch. Use read_more when supplied '
            'context is insufficient and list only concrete paths already present in the state. Use patch only when patch is complete: '
            'it must contain operation, path, old, and new. For create, old must be exactly "" and new must be complete file contents. '
            'For modify, old must be an exact substring from supplied file context and new must fully replace it. Never return a prose plan, '
            'pseudo-code, markdown diff, invented path, or partial file. Use finish only when complete or no safe action exists. '
            'Apply review_feedback and do not repeat a rejected patch.'
        )
        raw_text = self._invoke_responses_api(
            instructions=system_prompt,
            input_text=json.dumps(state, ensure_ascii=False, indent=2),
        )
        if not raw_text:
            return None

        payload = _extract_json_object(raw_text)
        if not isinstance(payload, dict):
            return None

        action = str(payload.get('action', '')).strip().lower()
        if action not in {'read_more', 'patch', 'finish'}:
            return None

        files_to_read: list[str] = []
        for value in payload.get('files_to_read', []) or []:
            if isinstance(value, str) and value.strip():
                files_to_read.append(value.strip())

        proposal_payload = payload.get('patch')
        proposal: PatchProposal | None = None
        if isinstance(proposal_payload, dict):
            operation = str(proposal_payload.get('operation', 'modify')).strip().lower()
            if operation not in {'modify', 'create'}:
                operation = 'modify'
            path = str(proposal_payload.get('path', '')).strip()
            old = str(proposal_payload.get('old', ''))
            new = str(proposal_payload.get('new', ''))
            if path and new and ((operation == 'create' and old == '') or (operation == 'modify' and old)):
                proposal = PatchProposal(path=path, old=old, new=new, operation=operation)

        return ReActDecision(
            action=action,
            summary=str(payload.get('summary', '')).strip(),
            rationale=str(payload.get('rationale', '')).strip(),
            files_to_read=files_to_read,
            proposal=proposal,
            model=self.model,
            provider=self.provider,
        )

    def plan_executor_tool(self, *, state: Mapping[str, Any]) -> ExecutorToolCall | None:
        if not self.enabled:
            return None

        system_prompt = (
            'You are the executor tool router for a code-repair agent. Return one JSON object only. '
            'Choose exactly one tool: read_file, write_file, run_tests, or finish. '
            'Use read_file only when the supplied context is insufficient and provide a workspace-relative path. '
            'Use write_file only when a complete safe patch is already available: provide path, old, and content. '
            'For a new file old must be empty and content must be the complete file. For a modification old must exactly match the supplied file. '
            'Use run_tests only after a write has been applied. Use finish when no tool action is needed. '
            'Never use arbitrary shell commands, absolute paths outside the workspace, prose, or partial content.'
        )
        raw_text = self._invoke_responses_api(
            instructions=system_prompt,
            input_text=json.dumps(state, ensure_ascii=False, indent=2),
        )
        payload = _extract_json_object(raw_text) if raw_text else None
        if not isinstance(payload, dict):
            return None
        tool = str(payload.get('tool', '')).strip().lower()
        if tool not in {'read_file', 'write_file', 'run_tests', 'finish'}:
            return None
        path = str(payload.get('path', '')).strip()
        old = str(payload.get('old', ''))
        content = str(payload.get('content', ''))
        if tool in {'read_file', 'write_file'} and not path:
            return None
        if tool == 'write_file' and not content:
            return None
        return ExecutorToolCall(
            tool=tool,
            path=path,
            old=old,
            content=content,
            summary=str(payload.get('summary', '')).strip(),
            model=self.model,
            provider=self.provider,
        )
    def review_result(self, *, state: Mapping[str, Any]) -> ReviewDecision | None:
        if not self.enabled:
            return None

        system_prompt = (
            'You are the reviewer in a collaborative code-repair loop. Return one JSON object only with keys '
            'action, summary, rationale, and files_to_read. Choose exactly approve, revise, or reject. '
            'Approve only when a patch was actually applied, changed_files reflect the intended change, and verification '
            'passed or was explicitly not applicable. No tests collected is not a passing test, but may be acceptable when '
            'there are no relevant tests and the rationale says verification was skipped. Choose revise when context is missing, '
            'the patch is absent or partial, the requested file was not changed, tests failed, or another safe correction is possible. '
            'Choose reject only for unsafe or fundamentally wrong work. Inspect patch, patch_applied, changed_files, baseline_test, '
            'latest_test, and branch history; do not infer success from a planner claim alone. List concrete files_to_read only when needed. '
            'When approving, summary must directly answer every explicit user deliverable from task_description. Include changed files, verification status, and requested analysis or complexity facts. '
            'Never return only Reviewer approved or Approved creation. If changed_files is empty, do not mention changed files or say none; include that field only when files actually changed.'
        )
        raw_text = self._invoke_responses_api(
            instructions=system_prompt,
            input_text=json.dumps(state, ensure_ascii=False, indent=2),
        )
        if not raw_text:
            return None

        payload = _extract_json_object(raw_text)
        if not isinstance(payload, dict):
            return None

        action = str(payload.get('action', '')).strip().lower()
        if action not in {'approve', 'revise', 'reject'}:
            return None

        files_to_read: list[str] = []
        for value in payload.get('files_to_read', []) or []:
            if isinstance(value, str) and value.strip():
                files_to_read.append(value.strip())

        return ReviewDecision(
            action=action,
            summary=str(payload.get('summary', '')).strip(),
            rationale=str(payload.get('rationale', '')).strip(),
            files_to_read=files_to_read,
            model=self.model,
            provider=self.provider,
        )

    def chat_task(
        self,
        *,
        context: Mapping[str, Any],
        user_message: str,
    ) -> ChatResponseResult:
        if self.enabled:
            prompt_payload = {
                'context': context,
                'user_message': user_message,
                'instructions': (
                    'Return one JSON object only. Reply in the user language as a concise senior coding assistant. '
                    'Use the provided context as the only source of truth. Distinguish confirmed facts from queued or in-progress work. '
                    'Never claim a file was created, changed, tested, or reviewed unless context contains matching confirmed events, '
                    'patch.applied data, changed_files, or test results. If implementation_request is true but work is not complete, '
                    'say it is queued or in progress and do not present the intended patch as an accomplished result. '
                    'If the user asks to inspect a panel, suggested_panel must be one of summary, diff, tests, timeline, retrieval, memory, plan, or artifacts; otherwise it must be null. '
                    'First classify the request as intent=implementation, question, panel, status, explanation, or unknown using the full context; do not use keyword matching alone. '
                    'Use intent=implementation only when the user is asking to create, change, fix, implement, or run repository work. Use intent=panel for panel navigation. '
                    'If the request is ambiguous, use unknown and do not trigger execution. implementation_request must be true exactly when intent=implementation. '
                    'The JSON object must include reply, intent, suggested_panel, and implementation_request. Do not expose hidden chain-of-thought; summarize observable progress only.'
                ),
            }
            system_prompt = (
                'You are the conversational interface for a code-repair agent. '
                'Answer directly in the user language and ground every status claim in the supplied context. '
                'The selected workspace in context is authoritative. Never invent files, paths, test results, or completion. '
                'Return one JSON object with keys reply, intent, suggested_panel, and implementation_request. The intent is the authoritative request classification.'
            )
            raw_text = self._invoke_responses_api(
                instructions=system_prompt,
                input_text=json.dumps(prompt_payload, ensure_ascii=False, indent=2),
            )
            if raw_text:
                payload = _extract_json_object(raw_text)
                if isinstance(payload, dict):
                    reply = str(payload.get('reply', '')).strip()
                    suggested_panel = str(payload.get('suggested_panel', '')).strip() or None
                    if suggested_panel not in {'summary', 'diff', 'tests', 'timeline', 'retrieval', 'memory', 'plan', 'artifacts', None}:
                        suggested_panel = None
                    intent = str(payload.get('intent', 'unknown')).strip().lower()
                    if intent not in {'implementation', 'question', 'panel', 'status', 'explanation', 'unknown'}:
                        intent = 'unknown'
                    implementation_request = intent == 'implementation'
                    if reply:
                        return ChatResponseResult(
                            reply=reply,
                            suggested_panel=suggested_panel,
                            intent=intent,
                            implementation_request=implementation_request,
                            model=self.model,
                            provider=self.provider,
                        )

        return ChatResponseResult(
            reply='The language model is unavailable, so I cannot safely classify this request for execution.',
            suggested_panel=None,
            intent='unknown',
            implementation_request=False,
            model=self.model or 'heuristic',
            provider='heuristic',
        )
    def review_chat_response(
        self,
        *,
        context: Mapping[str, Any],
        user_message: str,
        assistant_reply: str,
        implementation_request: bool,
    ) -> ChatResponseReviewResult:
        if self.enabled:
            prompt_payload = {
                'context': context,
                'user_message': user_message,
                'assistant_reply': assistant_reply,
                'implementation_request': implementation_request,
                'instructions': (
                    'Return one JSON object only. Judge whether the assistant reply already answers the user. '
                    'Set adequate to true only when it is concise, relevant, and supported by context. Set adequate to false when it misses the request, '
                    'is vague, or fails to reflect implementation intent. Mark it false if it claims a file was created or changed, tests passed, '
                    'or work completed without matching confirmation in context, or presents queued work as complete. When false, corrected_reply is required '
                    'and must directly address the user using only confirmed facts. Do not require or reproduce hidden chain-of-thought. '
                    'The JSON object must include adequate, corrected_reply, reason, and suggested_panel.'
                ),
            }
            system_prompt = (
                'You are a reviewer agent that checks whether a task assistant reply actually answered the user. '
                'Use context as the source of truth. Prefer the original reply only when its claims are supported. '
                'Reject unsupported completion, file-creation, test, review, or workspace claims and request a concise factual correction. '
                'Return one JSON object with keys adequate, corrected_reply, reason, and suggested_panel.'
            )
            raw_text = self._invoke_responses_api(
                instructions=system_prompt,
                input_text=json.dumps(prompt_payload, ensure_ascii=False, indent=2),
            )
            if raw_text:
                payload = _extract_json_object(raw_text)
                if isinstance(payload, dict):
                    adequate = self._coerce_bool(payload.get('adequate'))
                    corrected_reply = str(payload.get('corrected_reply', '')).strip() or None
                    reason = str(payload.get('reason', '')).strip()
                    suggested_panel = str(payload.get('suggested_panel', '')).strip() or None
                    if suggested_panel not in {'summary', 'diff', 'tests', 'timeline', 'retrieval', 'memory', 'plan', 'artifacts', None}:
                        suggested_panel = None
                    if not adequate and not corrected_reply:
                        corrected_reply = self._default_review_reply(user_message, context)
                    return ChatResponseReviewResult(
                        adequate=adequate,
                        corrected_reply=corrected_reply,
                        reason=reason,
                        suggested_panel=suggested_panel,
                        model=self.model,
                        provider=self.provider,
                    )

        heuristic_adequate = bool(assistant_reply.strip())
        heuristic_corrected = None if heuristic_adequate else self._default_review_reply(user_message, context)
        return ChatResponseReviewResult(
            adequate=heuristic_adequate,
            corrected_reply=heuristic_corrected,
            reason='heuristic review',
            suggested_panel=None,
            model=self.model or 'heuristic',
            provider='heuristic',
        )

    def _default_review_reply(self, user_message: str, context: Mapping[str, Any]) -> str:
        task = context.get('task') if isinstance(context, dict) else None
        task_title = str(task.get('title', 'the task')) if isinstance(task, dict) else 'the task'
        return f'I need more context to answer about `{task_title}` accurately.'
    def _heuristic_chat_reply(
        self,
        user_message: str,
        task_title: str,
        task_status: str,
        current_step: str | None,
        latest_diff: str | None,
        latest_test: dict[str, Any] | None,
    ) -> tuple[str, str | None, str, bool]:
        return (
            'The language model is unavailable, so I cannot safely classify this request for execution.',
            None,
            'unknown',
            False,
        )
    def _coerce_bool(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {'true', '1', 'yes', 'y', 'on'}
        if isinstance(value, (int, float)):
            return bool(value)
        return False

    def _build_heuristic_subgoals(
        self,
        task_title: str,
        task_description: str,
        focus_paths: list[str],
        retrieval_hits: list[dict[str, Any]],
        file_contexts: list[dict[str, Any]],
        memory_notes: list[dict[str, Any]],
    ) -> list[TaskSubgoalSpec]:
        inspection_files = self._dedupe_strings(
            focus_paths[:3]
            + [str(hit.get('path', '')).strip() for hit in retrieval_hits[:3]]
            + [str(entry.get('path', '')).strip() for entry in file_contexts[:2]]
        )
        implementation_files = self._dedupe_strings(
            [path for path in inspection_files if path]
            + [str(entry.get('path', '')).strip() for entry in file_contexts[2:4]]
        )
        verification_files = self._dedupe_strings(
            [path for path in inspection_files if path.startswith('tests/')]
            + [path for path in implementation_files if path.startswith('tests/')]
        )
        if not verification_files:
            verification_files = inspection_files[:2]

        memory_hint = ' Use prior memory to avoid repeating known failures.' if memory_notes else ''
        return [
            TaskSubgoalSpec(
                position=0,
                phase='inspect',
                title='Inspect the failing surface',
                description='Read the likely files, confirm the bug shape, and understand the current behavior.' if not task_description else f'Read the likely files and understand the task: {task_description.strip()}',
                success_criteria=[
                    'Relevant files have been read',
                    'The failing behavior is understood',
                    'The likely change surface is identified',
                ],
                files_to_read=inspection_files,
                rationale=f'Ground the task in the repository context before editing.{memory_hint}',
            ),
            TaskSubgoalSpec(
                position=1,
                phase='implement',
                title='Apply the smallest safe change',
                description='Produce the minimal patch that addresses the root cause without widening the change surface.' if not task_title else f'Patch the root cause for: {task_title.strip()}',
                success_criteria=[
                    'A focused patch has been applied',
                    'The changed code matches the task constraints',
                    'No unrelated code paths were expanded',
                ],
                files_to_read=implementation_files,
                rationale='Keep the edit small and reversible.',
            ),
            TaskSubgoalSpec(
                position=2,
                phase='verify',
                title='Verify and summarize the result',
                description='Run the checks, confirm the behavior, and capture the outcome in a short summary.',
                success_criteria=[
                    'Relevant tests have been run',
                    'The result is explained clearly',
                    'The final state is ready for handoff',
                ],
                files_to_read=verification_files,
                rationale='Close the loop with validation and a human-readable summary.',
            ),
        ]

    def _parse_subgoal_payload(self, payload: Any) -> list[TaskSubgoalSpec]:
        if not isinstance(payload, list):
            return []
        subgoals: list[TaskSubgoalSpec] = []
        for index, item in enumerate(payload[:3]):
            if not isinstance(item, dict):
                continue
            phase = str(item.get('phase', '')).strip() or ('inspect' if index == 0 else 'implement' if index == 1 else 'verify')
            title = str(item.get('title', '')).strip()
            description = str(item.get('description', '')).strip()
            success_criteria = [str(value).strip() for value in item.get('success_criteria', []) or [] if str(value).strip()]
            files_to_read = [str(value).strip() for value in item.get('files_to_read', []) or [] if str(value).strip()]
            rationale = str(item.get('rationale', '')).strip()
            if not title:
                continue
            subgoals.append(
                TaskSubgoalSpec(
                    position=index,
                    phase=phase,
                    title=title,
                    description=description,
                    success_criteria=success_criteria,
                    files_to_read=self._dedupe_strings(files_to_read),
                    rationale=rationale,
                )
            )
        return subgoals

    def _normalize_subgoal_positions(self, subgoals: list[TaskSubgoalSpec]) -> list[TaskSubgoalSpec]:
        normalized: list[TaskSubgoalSpec] = []
        for index, subgoal in enumerate(subgoals):
            normalized.append(
                TaskSubgoalSpec(
                    subgoal_id=subgoal.subgoal_id,
                    position=index,
                    phase=subgoal.phase,
                    title=subgoal.title,
                    description=subgoal.description,
                    success_criteria=subgoal.success_criteria,
                    files_to_read=subgoal.files_to_read,
                    rationale=subgoal.rationale,
                )
            )
        return normalized

    def _default_plan_summary(self, task_title: str) -> str:
        return f'Task plan prepared for {task_title.strip() or "the task"}.'

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                ordered.append(cleaned)
        return ordered

    def _invoke_responses_api(self, *, instructions: str, input_text: str) -> str:
        if self.provider == 'deepseek':
            payload = {
                'model': self.model,
                'messages': [
                    {'role': 'system', 'content': instructions},
                    {'role': 'user', 'content': input_text},
                ],
                'temperature': self.temperature,
                'max_tokens': self.max_output_tokens,
            }
            endpoint = f'{self.base_url}/chat/completions'
        else:
            payload = {
                'model': self.model,
                'instructions': instructions,
                'input': input_text,
                'temperature': self.temperature,
                'max_output_tokens': self.max_output_tokens,
            }
            endpoint = f'{self.base_url}/responses'
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'{self.provider} request failed: {exc.code} {detail}') from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f'{self.provider} request failed: {exc.reason}') from exc

        if isinstance(data, dict) and isinstance(data.get('error'), dict):
            error = data['error']
            message = error.get('message', 'Unknown error') if isinstance(error, dict) else 'Unknown error'
            raise RuntimeError(f'{self.provider} request failed: {message}')

        return _chat_completion_text(data) if self.provider == 'deepseek' else _response_text(data)


def _chat_completion_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ''
    choices = payload.get('choices', [])
    if not isinstance(choices, list) or not choices:
        return ''
    message = choices[0].get('message', {}) if isinstance(choices[0], dict) else {}
    content = message.get('content', '') if isinstance(message, dict) else ''
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return ''.join(str(item.get('text', '')) for item in content if isinstance(item, dict)).strip()
    return ''


def _response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ''

    output_text = payload.get('output_text')
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts: list[str] = []
    output = payload.get('output', [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get('content', [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                for key in ('text', 'output_text'):
                    value = block.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
    return '\n'.join(parts).strip()


def _extract_json_object(text: str) -> Any:
    cleaned = _strip_code_fences(text.strip())
    candidate = _first_json_object(cleaned)
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith('```'):
        stripped = re.sub(r'^```(?:json)?\s*', '', stripped, flags=re.IGNORECASE)
        stripped = re.sub(r'\s*```$', '', stripped)
    return stripped.strip()


def _first_json_object(text: str) -> str | None:
    start = text.find('{')
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None