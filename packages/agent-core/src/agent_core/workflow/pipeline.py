from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Mapping
import sys

from assistant_shared.models import CommandRule, MemoryRecord, PatchArtifact, RetrievalHit, TaskStep, TestOutcome
from assistant_tools import (
    artifact_from_patch,
    capture_snapshot,
    list_python_files,
    preview_text,
    read_text_file,
    replace_once,
    restore_snapshot,
    run_pytest,
    search_workspace_files,
    write_text_file,
)

from ..llm import LLMPlanResult, OpenAIPlanner, ReActDecision, ReviewDecision
from .parallel_branches import choose_parallel_branch
from .patch_model import PatchProposal


@dataclass(slots=True)
class WorkflowResult:
    summary: str
    patch: PatchArtifact | None
    baseline_test: TestOutcome
    final_test: TestOutcome
    snapshot_path: str | None
    changed_files: list[str]
    retrieval_hits: list[RetrievalHit]
    llm_used: bool
    plan_source: str
    llm_model: str | None
    react_turns: int
    branch_count: int
    review_decision: str


@dataclass(slots=True)
class ReactLoopResult:
    summary: str
    patch: PatchArtifact | None
    final_test: TestOutcome
    snapshot_path: Path | None
    changed_files: list[str]
    llm_used: bool
    plan_source: str
    llm_model: str | None
    react_turns: int
    branch_count: int
    review_decision: str


class DemoWorkflow:
    def __init__(
        self,
        workspace_root: str | Path,
        focus_paths: list[str] | None = None,
        snapshot_root: str | Path | None = None,
        allowed_commands: Iterable[CommandRule] | None = None,
        llm_config: Mapping[str, object] | None = None,
        memory_notes: list[MemoryRecord] | None = None,
    ):
        self.workspace_root = Path(workspace_root)
        self.focus_paths = focus_paths or ['src/demo_app/formatter.py']
        self.snapshot_root = Path(snapshot_root) if snapshot_root else self.workspace_root.parent / 'snapshots'
        self.allowed_commands = tuple(allowed_commands or ())
        self.llm_planner = OpenAIPlanner(llm_config)
        self.memory_notes = memory_notes or []

    async def run(
        self,
        *,
        task_id: str,
        task_title: str,
        task_description: str,
        emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]],
    ) -> WorkflowResult:
        await emit('task.started', TaskStep.read, 'Scanning the demo workspace', {'workspace_root': str(self.workspace_root)})

        retrieval_query = self._build_retrieval_query(task_title, task_description)
        await emit(
            'retrieval.started',
            TaskStep.read,
            'Searching the workspace for relevant code',
            {'query': retrieval_query, 'focus_paths': self.focus_paths},
        )
        retrieval_hits = search_workspace_files(
            self.workspace_root,
            retrieval_query,
            focus_paths=self.focus_paths,
            limit=6,
        )
        await emit(
            'retrieval.completed',
            TaskStep.read,
            f'Found {len(retrieval_hits)} relevant file(s)',
            {'query': retrieval_query, 'hits': [hit.model_dump(mode='json') for hit in retrieval_hits]},
        )

        discovered = self._read_context_files(retrieval_hits)
        for path, text in discovered.items():
            await emit(
                'file.read',
                TaskStep.read,
                f'Read {path}',
                {'path': path, 'preview': preview_text(text)},
            )

        baseline = await self._run_tests(emit=emit, step=TaskStep.analyze, label='baseline')
        await emit(
            'test.completed',
            TaskStep.analyze,
            'Captured baseline test results',
            {
                'passed': baseline.passed,
                'return_code': baseline.return_code,
                'stdout': baseline.stdout,
                'stderr': baseline.stderr,
                'command': baseline.command,
                'duration_ms': baseline.duration_ms,
            },
        )

        react = await self._run_collaborative_loop(
            task_id=task_id,
            task_title=task_title,
            task_description=task_description,
            retrieval_hits=retrieval_hits,
            discovered=discovered,
            baseline=baseline,
            emit=emit,
        )
        summary = react.summary
        await emit(
            'task.summarized',
            TaskStep.summarize,
            summary,
            {
                'summary': summary,
                'changed_files': react.changed_files,
                'retrieval_hits': len(retrieval_hits),
                'memory_notes': len(self.memory_notes),
                'plan_source': react.plan_source,
                'llm_used': react.llm_used,
                'react_turns': react.react_turns,
            },
        )

        return WorkflowResult(
            summary=summary,
            patch=react.patch,
            baseline_test=baseline,
            final_test=react.final_test,
            snapshot_path=str(react.snapshot_path) if react.snapshot_path else None,
            changed_files=react.changed_files,
            retrieval_hits=retrieval_hits,
            llm_used=react.llm_used,
            plan_source=react.plan_source,
            llm_model=react.llm_model,
            react_turns=react.react_turns,
            branch_count=react.branch_count,
            review_decision=react.review_decision,
        )

    def _build_retrieval_query(self, title: str, description: str) -> str:
        parts = [title.strip(), description.strip(), ' '.join(self.focus_paths)]
        return ' '.join(part for part in parts if part)

    def _read_context_files(self, retrieval_hits: list[RetrievalHit]) -> dict[str, str]:
        contents: dict[str, str] = {}
        for relative_path in self.focus_paths:
            file_path = self.workspace_root / relative_path
            if file_path.exists():
                contents[relative_path] = read_text_file(file_path)
        for hit in retrieval_hits:
            if hit.path in contents:
                continue
            file_path = self.workspace_root / hit.path
            if file_path.exists():
                contents[hit.path] = read_text_file(file_path)
        for candidate in list_python_files(self.workspace_root):
            relative = candidate.relative_to(self.workspace_root).as_posix()
            if relative not in contents and relative.startswith('tests/'):
                contents[relative] = read_text_file(candidate)
        return contents

    async def _run_collaborative_loop(
        self,
        *,
        task_id: str,
        task_title: str,
        task_description: str,
        retrieval_hits: list[RetrievalHit],
        discovered: dict[str, str],
        baseline: TestOutcome,
        emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]],
    ) -> ReactLoopResult:
        max_turns = 4
        react_turns = 0
        llm_used = False
        llm_model: str | None = None
        plan_source = 'heuristic'
        summary = ''
        patch: PatchArtifact | None = None
        final_test = baseline
        changed_files: list[str] = []
        snapshot_path: Path | None = None
        patch_applied = False
        current_context = dict(discovered)
        branch_history: list[dict[str, object]] = []
        review_feedback: dict[str, object] | None = None
        review_decision = 'pending'

        for turn in range(1, max_turns + 1):
            react_turns = turn
            selection = await choose_parallel_branch(
                self,
                turn=turn,
                task_title=task_title,
                task_description=task_description,
                retrieval_hits=retrieval_hits,
                discovered=current_context,
                baseline=baseline,
                latest_test=final_test,
                patch_applied=patch_applied,
                changed_files=changed_files,
                review_feedback=review_feedback,
                branch_history=branch_history,
                emit=emit,
            )
            decision = selection.decision
            branch_name = selection.branch_id
            llm_used = llm_used or any(candidate.decision.provider == 'openai' for candidate in selection.candidates)
            if decision.provider and decision.model:
                llm_model = decision.model
                plan_source = f'{decision.provider}:{decision.model}'

            branch_history.append(
                {
                    'turn': turn,
                    'branch': branch_name,
                    'agent': selection.agent,
                    'profile': selection.profile,
                    'action': decision.action,
                    'summary': decision.summary,
                    'rationale': decision.rationale,
                    'files_to_read': decision.files_to_read,
                    'score': selection.score,
                    'selected': True,
                    'candidates': [
                        {
                            'branch': candidate.branch_id,
                            'agent': candidate.agent,
                            'profile': candidate.profile,
                            'action': candidate.decision.action,
                            'summary': candidate.decision.summary,
                            'rationale': candidate.decision.rationale,
                            'files_to_read': candidate.decision.files_to_read,
                            'score': candidate.score,
                            'selected': candidate.selected,
                        }
                        for candidate in selection.candidates
                    ],
                }
            )

            await emit(
                'agent.planner.completed',
                TaskStep.analyze,
                decision.summary or 'Planner chose the current branch.',
                {
                    'agent': selection.agent,
                    'agent_profile': selection.profile,
                    'branch': branch_name,
                    'selected_branch': branch_name,
                    'action': decision.action,
                    'rationale': decision.rationale,
                    'files_to_read': decision.files_to_read,
                    'score': selection.score,
                },
            )

            if decision.action == 'read_more':
                newly_read = self._read_requested_files(decision.files_to_read, current_context)
                if newly_read:
                    current_context.update(newly_read)
                    for path, text in newly_read.items():
                        await emit(
                            'file.read',
                            TaskStep.read,
                            f'Read {path}',
                            {'path': path, 'preview': preview_text(text)},
                        )
                review_feedback = {
                    'action': 'revise',
                    'branch': branch_name,
                    'summary': decision.summary,
                    'rationale': decision.rationale,
                    'files_to_read': decision.files_to_read,
                }
                continue

            proposal = None
            if decision.action == 'patch':
                proposal = self._resolve_react_proposal(decision, current_context, final_test)
                if proposal is None:
                    await emit(
                        'llm.plan.invalid',
                        TaskStep.analyze,
                        'The planner returned an unsafe or unusable patch',
                        {
                            'agent': selection.agent,
                            'agent_profile': selection.profile,
                            'branch': branch_name,
                            'selected_branch': branch_name,
                            'action': decision.action,
                            'rationale': decision.rationale,
                            'files_to_read': decision.files_to_read,
                            'score': selection.score,
                        },
                    )
                    review_feedback = {
                        'action': 'revise',
                        'branch': branch_name,
                        'summary': 'The proposed patch was unsafe or unusable.',
                        'rationale': decision.rationale,
                    }
                    continue

                snapshot_path = self.snapshot_root / task_id / branch_name / 'before_patch'
                capture_snapshot(self.workspace_root, snapshot_path)
                patch_applied = True
                await emit(
                    'snapshot.created',
                    TaskStep.patch,
                    'Captured a rollback snapshot',
                    {'snapshot_path': str(snapshot_path), 'branch': branch_name},
                )

                await emit(
                    'agent.executor.started',
                    TaskStep.patch,
                    f'Executor is applying {branch_name}',
                    {
                        'agent': 'executor',
                        'branch': branch_name,
                        'proposal': {
                            'path': proposal.path,
                            'old': proposal.old,
                            'new': proposal.new,
                        },
                    },
                )

                before = read_text_file(proposal.path)
                after = replace_once(before, proposal.old, proposal.new)
                write_text_file(proposal.path, after)
                patch = artifact_from_patch(proposal.path, before, after)
                changed_files = [proposal.path]
                current_relative = Path(proposal.path).resolve().relative_to(self.workspace_root.resolve()).as_posix()
                current_context[current_relative] = after
                await emit(
                    'patch.applied',
                    TaskStep.patch,
                    f'Patched {proposal.path}',
                    {'path': proposal.path, 'diff': patch.diff, 'rationale': decision.rationale, 'branch': branch_name},
                )

                final_test = await self._run_tests(emit=emit, step=TaskStep.test, label=f'branch-{turn}')
                await emit(
                    'test.completed',
                    TaskStep.test,
                    'Ran the verification test suite',
                    {
                        'passed': final_test.passed,
                        'return_code': final_test.return_code,
                        'stdout': final_test.stdout,
                        'stderr': final_test.stderr,
                        'command': final_test.command,
                        'duration_ms': final_test.duration_ms,
                        'branch': branch_name,
                    },
                )
                await emit(
                    'agent.executor.completed',
                    TaskStep.test,
                    f'Executor finished {branch_name}',
                    {
                        'agent': 'executor',
                        'branch': branch_name,
                        'passed': final_test.passed,
                        'patch_applied': True,
                        'changed_files': changed_files,
                    },
                )
                await emit(
                    'react.observation',
                    TaskStep.test,
                    'The workspace was tested after the patch',
                    {
                        'action': 'patch',
                        'passed': final_test.passed,
                        'rationale': decision.rationale,
                        'summary': decision.summary,
                        'branch': branch_name,
                    },
                )

            review = await self._review_react_decision(
                branch_name=branch_name,
                task_title=task_title,
                task_description=task_description,
                decision=decision,
                proposal=proposal,
                final_test=final_test,
                changed_files=changed_files,
                retrieval_hits=retrieval_hits,
                current_context=current_context,
                baseline=baseline,
                branch_history=branch_history,
                emit=emit,
            )
            if review.provider:
                llm_used = llm_used or review.provider == 'openai'
            if review.model:
                llm_model = review.model
                plan_source = f'{review.provider}:{review.model}'
            review_decision = review.action

            if review.action == 'approve' and final_test.passed:
                summary = review.summary or decision.summary or self._summarize_outcome(final_test, changed_files)
                await emit(
                    'react.observation',
                    TaskStep.review,
                    summary,
                    {
                        'action': 'approve',
                        'branch': branch_name,
                        'rationale': review.rationale,
                        'summary': review.summary,
                    },
                )
                break

            if review.action in {'revise', 'reject'}:
                await emit(
                    'replan.requested',
                    TaskStep.analyze,
                    'Reviewer requested a new branch',
                    {
                        'branch': branch_name,
                        'action': review.action,
                        'rationale': review.rationale,
                        'summary': review.summary,
                        'files_to_read': review.files_to_read,
                    },
                )
                if snapshot_path is not None:
                    restore_snapshot(snapshot_path, self.workspace_root)
                    await emit(
                        'branch.reverted',
                        TaskStep.rollback,
                        f'Restored the workspace after {branch_name}',
                        {'branch': branch_name, 'snapshot_path': str(snapshot_path)},
                    )
                current_context = self._read_context_files(retrieval_hits)
                if review.files_to_read:
                    newly_read = self._read_requested_files(review.files_to_read, current_context)
                    if newly_read:
                        current_context.update(newly_read)
                        for path, text in newly_read.items():
                            await emit(
                                'file.read',
                                TaskStep.read,
                                f'Read {path}',
                                {'path': path, 'preview': preview_text(text)},
                            )
                patch_applied = False
                changed_files = []
                final_test = baseline
                review_feedback = {
                    'action': review.action,
                    'branch': branch_name,
                    'summary': review.summary,
                    'rationale': review.rationale,
                    'files_to_read': review.files_to_read,
                }
                continue

            summary = review.summary or decision.summary or self._summarize_outcome(final_test, changed_files)
            await emit(
                'react.observation',
                TaskStep.review,
                summary,
                {
                    'action': review.action,
                    'branch': branch_name,
                    'rationale': review.rationale,
                    'summary': review.summary,
                },
            )
            if review.action == 'approve':
                break

        if not summary:
            summary = self._summarize_outcome(final_test, changed_files)

        return ReactLoopResult(
            summary=summary,
            patch=patch,
            final_test=final_test,
            snapshot_path=snapshot_path,
            changed_files=changed_files,
            llm_used=llm_used,
            plan_source=plan_source,
            llm_model=llm_model,
            react_turns=react_turns,
            branch_count=self._count_explored_branches(branch_history),
            review_decision=review_decision,
        )

    async def _plan_react_decision(
        self,
        *,
        task_title: str,
        task_description: str,
        retrieval_hits: list[RetrievalHit],
        memory_notes: list[MemoryRecord],
        discovered: dict[str, str],
        baseline: TestOutcome,
        latest_test: TestOutcome,
        patch_applied: bool,
        changed_files: list[str],
        review_feedback: dict[str, object] | None,
        branch_history: list[dict[str, object]],
        emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]],
    ) -> ReActDecision | None:
        await emit(
            'agent.planner.started',
            TaskStep.analyze,
            'Planner is preparing the next branch',
            {
                'agent': 'planner',
                'provider': self.llm_planner.provider,
                'model': self.llm_planner.model,
                'enabled': self.llm_planner.enabled,
            },
        )
        if not self.llm_planner.enabled:
            await emit(
                'llm.plan.skipped',
                TaskStep.analyze,
                'LLM planner is disabled; using deterministic fallback',
                {'reason': self.llm_planner.disabled_reason},
            )
            return None

        await emit(
            'llm.plan.started',
            TaskStep.analyze,
            'Requesting the next ReAct step from the LLM',
            {'provider': self.llm_planner.provider, 'model': self.llm_planner.model},
        )
        decision = await _to_thread(
            self.llm_planner.plan_next_step,
            state=self._build_react_state(
                task_title=task_title,
                task_description=task_description,
                retrieval_hits=retrieval_hits,
                memory_notes=memory_notes,
                discovered=discovered,
                baseline=baseline,
                latest_test=latest_test,
                patch_applied=patch_applied,
                changed_files=changed_files,
                review_feedback=review_feedback,
                branch_history=branch_history,
            ),
        )
        if decision is None:
            await emit(
                'llm.plan.completed',
                TaskStep.analyze,
                'The LLM did not return a usable ReAct decision',
                {'used': False, 'provider': self.llm_planner.provider, 'model': self.llm_planner.model},
            )
            return None

        await emit(
            'llm.plan.completed',
            TaskStep.analyze,
            'Model produced a ReAct decision',
            {
                'used': True,
                'provider': decision.provider,
                'model': decision.model,
                'action': decision.action,
                'files_to_read': decision.files_to_read,
                'has_patch': decision.proposal is not None,
                'summary': decision.summary,
                'rationale': decision.rationale,
            },
        )
        return decision

    async def _review_react_decision(
        self,
        *,
        branch_name: str,
        task_title: str,
        task_description: str,
        decision: ReActDecision,
        proposal: PatchProposal | None,
        final_test: TestOutcome,
        changed_files: list[str],
        retrieval_hits: list[RetrievalHit],
        current_context: dict[str, str],
        baseline: TestOutcome,
        branch_history: list[dict[str, object]],
        emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]],
    ) -> ReviewDecision:
        if not self.llm_planner.enabled:
            review = self._fallback_review_decision(
                branch_name=branch_name,
                decision=decision,
                proposal=proposal,
                final_test=final_test,
                changed_files=changed_files,
            )
            await emit(
                'agent.reviewer.started',
                TaskStep.review,
                'Reviewer is checking the latest branch',
                {
                    'agent': 'reviewer',
                    'branch': branch_name,
                    'reason': self.llm_planner.disabled_reason,
                },
            )
            await emit(
                'agent.reviewer.completed',
                TaskStep.review,
                review.summary or 'Reviewer finished the branch check',
                {
                    'agent': 'reviewer',
                    'branch': branch_name,
                    'action': review.action,
                    'rationale': review.rationale,
                    'summary': review.summary,
                },
            )
            return review

        await emit(
            'agent.reviewer.started',
            TaskStep.review,
            'Reviewer is checking the latest branch',
            {
                'agent': 'reviewer',
                'branch': branch_name,
                'planner_action': decision.action,
                'has_patch': proposal is not None,
            },
        )
        review = await _to_thread(
            self.llm_planner.review_result,
            state={
                **self._build_react_state(
                    task_title=task_title,
                    task_description=task_description,
                    retrieval_hits=retrieval_hits,
                    memory_notes=self.memory_notes,
                    discovered=current_context,
                    baseline=baseline,
                    latest_test=final_test,
                    patch_applied=proposal is not None,
                    changed_files=changed_files,
                    review_feedback={
                        'branch': branch_name,
                        'planner_action': decision.action,
                        'planner_summary': decision.summary,
                        'planner_rationale': decision.rationale,
                    },
                    branch_history=branch_history,
                ),
                'branch_name': branch_name,
                'planner_decision': {
                    'action': decision.action,
                    'summary': decision.summary,
                    'rationale': decision.rationale,
                    'files_to_read': decision.files_to_read,
                },
                'patch': None if proposal is None else {
                    'path': proposal.path,
                    'old': proposal.old,
                    'new': proposal.new,
                },
            },
        )
        if review is None:
            review = self._fallback_review_decision(
                branch_name=branch_name,
                decision=decision,
                proposal=proposal,
                final_test=final_test,
                changed_files=changed_files,
            )

        await emit(
            'agent.reviewer.completed',
            TaskStep.review,
            review.summary or 'Reviewer finished the branch check',
            {
                'agent': 'reviewer',
                'branch': branch_name,
                'action': review.action,
                'rationale': review.rationale,
                'summary': review.summary,
                'files_to_read': review.files_to_read,
            },
        )
        return review

    def _fallback_react_decision(
        self,
        discovered: dict[str, str],
        latest_test: TestOutcome,
        patch_applied: bool,
    ) -> ReActDecision:
        if not patch_applied or not latest_test.passed:
            proposal = self._infer_patch(discovered, latest_test)
            if proposal is not None:
                return ReActDecision(
                    action='patch',
                    summary='Patch verified successfully.' if latest_test.passed else 'Applying the safest obvious patch.',
                    rationale='Deterministic fallback based on local code patterns.',
                    proposal=proposal,
                    provider='heuristic',
                    model='heuristic',
                )

        return ReActDecision(
            action='finish',
            summary=self._summarize_outcome(latest_test, []),
            rationale='Deterministic fallback converged.',
            provider='heuristic',
            model='heuristic',
        )

    def _fallback_review_decision(
        self,
        *,
        branch_name: str,
        decision: ReActDecision,
        proposal: PatchProposal | None,
        final_test: TestOutcome,
        changed_files: list[str],
    ) -> ReviewDecision:
        if final_test.passed:
            return ReviewDecision(
                action='approve',
                summary=self._summarize_outcome(final_test, changed_files),
                rationale='Deterministic reviewer accepted the passing branch.',
                model='heuristic',
                provider='heuristic',
            )

        return ReviewDecision(
            action='revise',
            summary='The latest branch still needs work.',
            rationale='Deterministic reviewer requested another branch.',
            model='heuristic',
            provider='heuristic',
        )

    def _build_react_state(
        self,
        *,
        task_title: str,
        task_description: str,
        retrieval_hits: list[RetrievalHit],
        memory_notes: list[MemoryRecord],
        discovered: dict[str, str],
        baseline: TestOutcome,
        latest_test: TestOutcome,
        patch_applied: bool,
        changed_files: list[str],
        review_feedback: dict[str, object] | None = None,
        branch_history: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return {
            'task_title': task_title,
            'task_description': task_description,
            'retrieval_hits': [hit.model_dump(mode='json') for hit in retrieval_hits],
            'memory_notes': [note.model_dump(mode='json') for note in memory_notes],
            'file_contexts': [
                {'path': path, 'content': content[:5000]}
                for path, content in discovered.items()
            ],
            'baseline_test': baseline.model_dump(mode='json'),
            'latest_test': latest_test.model_dump(mode='json'),
            'patch_applied': patch_applied,
            'changed_files': changed_files,
            'review_feedback': review_feedback or {},
            'branch_history': branch_history or [],
            'available_actions': ['read_more', 'patch', 'finish'],
        }

    def _count_explored_branches(self, branch_history: list[dict[str, object]]) -> int:
        total = 0
        for entry in branch_history:
            candidates = entry.get('candidates') if isinstance(entry, dict) else None
            if isinstance(candidates, list) and candidates:
                total += len(candidates)
            else:
                total += 1
        return total

    def _read_requested_files(self, files_to_read: list[str], current_context: dict[str, str]) -> dict[str, str]:
        newly_read: dict[str, str] = {}
        for relative_path in files_to_read:
            candidate = self._resolve_workspace_path(relative_path)
            if candidate is None or not candidate.exists():
                continue
            relative = candidate.relative_to(self.workspace_root).as_posix()
            if relative in current_context or relative in newly_read:
                continue
            newly_read[relative] = read_text_file(candidate)
        return newly_read

    def _resolve_react_proposal(
        self,
        decision: ReActDecision,
        discovered: dict[str, str],
        latest_test: TestOutcome,
    ) -> PatchProposal | None:
        if decision.proposal is not None:
            normalized = self._normalize_llm_proposal(decision.proposal, discovered)
            if normalized is not None:
                return normalized
        return self._infer_patch(discovered, latest_test)

    def _summarize_outcome(self, latest_test: TestOutcome, changed_files: list[str]) -> str:
        if latest_test.passed:
            if changed_files:
                return f'Patch verified successfully for {", ".join(changed_files)}.'
            return 'Patch verified successfully.'
        return 'Patch applied, but tests still fail.'

    def _build_patch_proposal(
        self,
        llm_plan: LLMPlanResult | None,
        discovered: dict[str, str],
        baseline: TestOutcome,
    ) -> PatchProposal | None:
        if llm_plan is not None:
            proposal = self._normalize_llm_proposal(llm_plan.proposal, discovered)
            if proposal is not None:
                return proposal
        return self._infer_patch(discovered, baseline)

    def _normalize_llm_proposal(
        self,
        proposal: PatchProposal,
        discovered: dict[str, str],
    ) -> PatchProposal | None:
        candidate = self._resolve_workspace_path(proposal.path)
        if candidate is None or not candidate.exists():
            return None

        relative = candidate.relative_to(self.workspace_root).as_posix()
        content = discovered.get(relative)
        if content is None:
            content = read_text_file(candidate)
        if proposal.old not in content:
            return None

        return PatchProposal(path=str(candidate), old=proposal.old, new=proposal.new)

    def _resolve_workspace_path(self, path: str | Path) -> Path | None:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        try:
            candidate.resolve().relative_to(self.workspace_root.resolve())
        except Exception:
            return None
        return candidate

    async def _maybe_plan_with_llm(
        self,
        *,
        task_title: str,
        task_description: str,
        retrieval_hits: list[RetrievalHit],
        memory_notes: list[MemoryRecord],
        discovered: dict[str, str],
        baseline: TestOutcome,
        emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]],
    ) -> LLMPlanResult | None:
        if not self.llm_planner.enabled:
            await emit(
                'llm.plan.skipped',
                TaskStep.analyze,
                'LLM planner is disabled; using deterministic fallback',
                {'reason': self.llm_planner.disabled_reason},
            )
            return None

        await emit(
            'llm.plan.started',
            TaskStep.analyze,
            'Requesting a patch proposal from the LLM',
            {'provider': self.llm_planner.provider, 'model': self.llm_planner.model},
        )
        result = await _to_thread(
            self.llm_planner.propose_patch,
            task_title=task_title,
            task_description=task_description,
            retrieval_hits=[hit.model_dump(mode='json') for hit in retrieval_hits],
            memory_notes=[note.model_dump(mode='json') for note in memory_notes],
            file_contexts=[{'path': path, 'content': content} for path, content in discovered.items()],
            baseline_test=baseline.model_dump(mode='json'),
        )
        if result is None:
            await emit(
                'llm.plan.completed',
                TaskStep.analyze,
                'The LLM did not return a usable patch',
                {'used': False, 'provider': self.llm_planner.provider, 'model': self.llm_planner.model},
            )
            return None

        await emit(
            'llm.plan.completed',
            TaskStep.analyze,
            'Model produced a patch proposal',
            {
                'used': True,
                'provider': result.provider,
                'model': result.model,
                'path': result.proposal.path,
                'summary': result.summary,
                'rationale': result.rationale,
            },
        )
        return result

    def _infer_patch(self, discovered: dict[str, str], baseline: TestOutcome) -> PatchProposal | None:
        for relative_path, text in discovered.items():
            if '.upper()' in text:
                return PatchProposal(path=str(self.workspace_root / relative_path), old='.upper()', new='.lower()')
            if 'strip().upper()' in text:
                return PatchProposal(path=str(self.workspace_root / relative_path), old='strip().upper()', new='strip().lower()')
        if 'upper' in baseline.stdout.lower() or 'upper' in baseline.stderr.lower():
            for relative_path, text in discovered.items():
                if 'upper()' in text:
                    return PatchProposal(path=str(self.workspace_root / relative_path), old='.upper()', new='.lower()')
        return None

    async def _run_tests(
        self,
        *,
        emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]],
        step: TaskStep,
        label: str,
    ) -> TestOutcome:
        command = [sys.executable, '-m', 'pytest', '-q']
        await emit(
            'command.started',
            step,
            f'Running {label} verification command',
            {'command': command, 'workspace_root': str(self.workspace_root)},
        )
        outcome = await _to_thread(run_pytest, self.workspace_root, allowed_commands=self.allowed_commands or None)
        await emit(
            'command.completed',
            step,
            f'{label.title()} verification command completed',
            {
                'command': outcome.command,
                'return_code': outcome.return_code,
                'passed': outcome.passed,
                'duration_ms': outcome.duration_ms,
            },
        )
        return outcome


async def _to_thread(func, *args, **kwargs):
    import asyncio

    return await asyncio.to_thread(func, *args, **kwargs)
