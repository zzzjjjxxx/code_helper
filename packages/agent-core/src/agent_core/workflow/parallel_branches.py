from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Any

from assistant_shared.models import RetrievalHit, TaskStep, TestOutcome

from ..llm import ReActDecision


@dataclass(slots=True)
class BranchCandidate:
    branch_id: str
    agent: str
    profile: str
    decision: ReActDecision
    score: float
    selected: bool = False


@dataclass(slots=True)
class ParallelBranchSelection:
    branch_id: str
    agent: str
    profile: str
    decision: ReActDecision
    score: float
    candidates: list[BranchCandidate]


async def choose_parallel_branch(
    # DOC_ANCHOR: parallel_branches.choose
    workflow: Any,
    *,
    turn: int,
    task_title: str,
    task_description: str,
    retrieval_hits: list[RetrievalHit],
    discovered: dict[str, str],
    baseline: TestOutcome,
    latest_test: TestOutcome,
    patch_applied: bool,
    changed_files: list[str],
    review_feedback: dict[str, object] | None,
    branch_history: list[dict[str, object]],
    emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]],
) -> ParallelBranchSelection:
    specs = [
        {
            'agent': 'planner',
            'profile': 'balanced',
            'hint': 'Choose the smallest safe patch that makes the tests pass.',
        },
        {
            'agent': 'critic',
            'profile': 'conservative',
            'hint': 'Prefer more context or a narrower change when uncertain.',
        },
        {
            'agent': 'memory',
            'profile': 'memory-aware',
            'hint': 'Use prior memory and related files to avoid repeat mistakes.',
        },
        {
            'agent': 'explorer',
            'profile': 'broad-context',
            'hint': 'Widen the search when the current branch does not explain the failure.',
        },
        {
            'agent': 'verifier',
            'profile': 'test-focused',
            'hint': 'Favor reading tests, validating the fix, and ending only when the state is safe.',
        },
    ]

    if not workflow.llm_planner.enabled:
        await emit(
            'llm.plan.skipped',
            TaskStep.analyze,
            'Parallel planner is disabled; using heuristic branches',
            {
                'reason': workflow.llm_planner.disabled_reason,
                'turn': turn,
                'agent_count': len(specs),
            },
        )

    plans = [
        _build_candidate(
            workflow,
            turn=turn,
            task_title=task_title,
            task_description=task_description,
            retrieval_hits=retrieval_hits,
            discovered=discovered,
            baseline=baseline,
            latest_test=latest_test,
            patch_applied=patch_applied,
            changed_files=changed_files,
            review_feedback=review_feedback,
            branch_history=branch_history,
            spec=spec,
            emit=emit,
        )
        for spec in specs
    ]
    candidates = await asyncio.gather(*plans)
    selected = max(candidates, key=lambda item: _score_candidate(workflow, item, latest_test=latest_test, patch_applied=patch_applied, changed_files=changed_files, discovered=discovered))
    for candidate in candidates:
        candidate.selected = candidate.branch_id == selected.branch_id

    await emit(
        'branch.selected',
        TaskStep.analyze,
        f'Selected {selected.branch_id}',
        {
            'branch': selected.branch_id,
            'turn': turn,
            'agent': selected.agent,
            'profile': selected.profile,
            'action': selected.decision.action,
            'summary': selected.decision.summary,
            'rationale': selected.decision.rationale,
            'candidate_count': len(candidates),
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
                for candidate in candidates
            ],
            'score': selected.score,
        },
    )
    return ParallelBranchSelection(
        branch_id=selected.branch_id,
        agent=selected.agent,
        profile=selected.profile,
        decision=selected.decision,
        score=selected.score,
        candidates=candidates,
    )


async def _build_candidate(
    # DOC_ANCHOR: parallel_branches.build_candidate
    workflow: Any,
    *,
    turn: int,
    task_title: str,
    task_description: str,
    retrieval_hits: list[RetrievalHit],
    discovered: dict[str, str],
    baseline: TestOutcome,
    latest_test: TestOutcome,
    patch_applied: bool,
    changed_files: list[str],
    review_feedback: dict[str, object] | None,
    branch_history: list[dict[str, object]],
    spec: dict[str, str],
    emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]],
) -> BranchCandidate:
    branch_id = f"turn-{turn}-{spec['agent']}"
    await emit(
        'agent.planner.started',
        TaskStep.analyze,
        'Planner is preparing the next branch',
        {
            'agent': spec['agent'],
            'profile': spec['profile'],
            'hint': spec['hint'],
            'provider': workflow.llm_planner.provider,
            'model': workflow.llm_planner.model,
            'enabled': workflow.llm_planner.enabled,
        },
    )

    decision = None
    if workflow.llm_planner.enabled:
        await emit(
            'llm.plan.started',
            TaskStep.analyze,
            'Requesting the next ReAct step from the LLM',
            {'provider': workflow.llm_planner.provider, 'model': workflow.llm_planner.model, 'agent': spec['agent'], 'profile': spec['profile']},
        )
        state = {
            **workflow._build_react_state(
                task_title=task_title,
                task_description=task_description,
                retrieval_hits=retrieval_hits,
                memory_notes=workflow.memory_notes,
                discovered=discovered,
                baseline=baseline,
                latest_test=latest_test,
                patch_applied=patch_applied,
                changed_files=changed_files,
                review_feedback=review_feedback,
                branch_history=branch_history,
            ),
            'planner_agent': spec['agent'],
            'planner_profile': spec['profile'],
            'planning_hint': spec['hint'],
            'turn': turn,
            'branch_id': branch_id,
        }
        decision = await asyncio.to_thread(workflow.llm_planner.plan_next_step, state=state)
        if decision is not None:
            await emit(
                'llm.plan.completed',
                TaskStep.analyze,
                'Model produced a ReAct decision',
                {
                    'used': True,
                    'provider': decision.provider,
                    'model': decision.model,
                    'agent': spec['agent'],
                    'profile': spec['profile'],
                    'action': decision.action,
                    'files_to_read': decision.files_to_read,
                    'has_patch': decision.proposal is not None,
                    'summary': decision.summary,
                    'rationale': decision.rationale,
                },
            )

    if decision is None:
        decision = _fallback_branch_decision(
            workflow,
            agent=spec['agent'],
            discovered=discovered,
            latest_test=latest_test,
            patch_applied=patch_applied,
        )

    score = _score_candidate(workflow, BranchCandidate(branch_id=branch_id, agent=spec['agent'], profile=spec['profile'], decision=decision, score=0.0), latest_test=latest_test, patch_applied=patch_applied, changed_files=changed_files, discovered=discovered)
    await emit(
        'branch.created',
        TaskStep.analyze,
        f'Created {branch_id}',
        {
            'branch': branch_id,
            'turn': turn,
            'agent': spec['agent'],
            'profile': spec['profile'],
            'action': decision.action,
            'summary': decision.summary,
            'rationale': decision.rationale,
            'files_to_read': decision.files_to_read,
            'score': score,
            'selected': False,
        },
    )
    return BranchCandidate(branch_id=branch_id, agent=spec['agent'], profile=spec['profile'], decision=decision, score=score)


def _fallback_branch_decision(
    # DOC_ANCHOR: parallel_branches.fallback
    workflow: Any,
    *,
    agent: str,
    discovered: dict[str, str],
    latest_test: TestOutcome,
    patch_applied: bool,
) -> ReActDecision:
    if agent == 'critic':
        files = [path for path in discovered if path.startswith('tests/')]
        if not files:
            files = [path for path in workflow.focus_paths if Path(path).suffix == '.py']
        if not latest_test.passed:
            return ReActDecision(
                action='read_more',
                summary='Read the surrounding tests before editing.',
                rationale='Conservative branch asks for more context.',
                files_to_read=files[:3],
                provider='heuristic',
                model='heuristic',
            )
        return ReActDecision(
            action='finish',
            summary=workflow._summarize_outcome(latest_test, []),
            rationale='Conservative branch accepts the current passing state.',
            provider='heuristic',
            model='heuristic',
        )

    if agent == 'memory':
        proposal = workflow._infer_patch(discovered, latest_test)
        if proposal is not None:
            return ReActDecision(
                action='patch',
                summary='Apply the memory-backed fix.',
                rationale='Memory-aware branch matched a known safe edit.',
                proposal=proposal,
                provider='heuristic',
                model='heuristic',
            )
        files: list[str] = []
        for note in workflow.memory_notes:
            files.extend(note.related_files)
        files = workflow._dedupe([item for item in files if item])
        if not files:
            files = workflow.focus_paths[:2]
        return ReActDecision(
            action='read_more',
            summary='Inspect files related to prior fixes.',
            rationale='Memory-aware branch wants more context from stored lessons.',
            files_to_read=files[:3],
            provider='heuristic',
            model='heuristic',
        )

    if agent == 'explorer':
        files = [path for path in discovered if path.startswith('tests/')]
        if not files:
            files = [path for path in workflow.focus_paths if Path(path).suffix == '.py']
        for note in workflow.memory_notes:
            files.extend(note.related_files)
        files = workflow._dedupe([item for item in files if item])
        if not latest_test.passed:
            return ReActDecision(
                action='read_more',
                summary='Explore more surrounding code before patching.',
                rationale='Explorer branch widens the search to avoid a premature fix.',
                files_to_read=files[:4],
                provider='heuristic',
                model='heuristic',
            )
        proposal = workflow._infer_patch(discovered, latest_test)
        if proposal is not None:
            return ReActDecision(
                action='patch',
                summary='Apply the fix after confirming the wider context.',
                rationale='Explorer branch found enough surrounding evidence for a patch.',
                proposal=proposal,
                provider='heuristic',
                model='heuristic',
            )
        return ReActDecision(
            action='finish',
            summary=workflow._summarize_outcome(latest_test, []),
            rationale='Explorer branch found no additional risky change.',
            provider='heuristic',
            model='heuristic',
        )

    if agent == 'verifier':
        files = [path for path in discovered if path.startswith('tests/')]
        if not files:
            files = [path for path in workflow.focus_paths if Path(path).suffix == '.py']
        if latest_test.passed:
            return ReActDecision(
                action='finish',
                summary=workflow._summarize_outcome(latest_test, []),
                rationale='Verifier branch accepts the current passing state.',
                provider='heuristic',
                model='heuristic',
            )
        return ReActDecision(
            action='read_more',
            summary='Read tests and verification surfaces before committing to a patch.',
            rationale='Verifier branch is focused on validation evidence.',
            files_to_read=files[:4],
            provider='heuristic',
            model='heuristic',
        )

    if not patch_applied or not latest_test.passed:
        proposal = workflow._infer_patch(discovered, latest_test)
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
        summary=workflow._summarize_outcome(latest_test, []),
        rationale='Deterministic fallback converged.',
        provider='heuristic',
        model='heuristic',
    )


def _score_candidate(
    # DOC_ANCHOR: parallel_branches.score
    workflow: Any,
    candidate: BranchCandidate,
    *,
    latest_test: TestOutcome,
    patch_applied: bool,
    changed_files: list[str],
    discovered: dict[str, str],
) -> float:
    score = candidate.score
    decision = candidate.decision
    if decision.action == 'patch':
        score += 10.0
    elif decision.action == 'finish':
        score += 12.0 if latest_test.passed else -3.0
    elif decision.action == 'read_more':
        score += 6.0 if not latest_test.passed else 2.0

    if decision.proposal is not None:
        score += 6.0
    if decision.files_to_read:
        score += min(len(decision.files_to_read), 3) * 0.5
    if candidate.agent == 'planner':
        score += 1.5
    elif candidate.agent == 'memory' and workflow.memory_notes:
        score += 1.0
    elif candidate.agent == 'critic':
        score += 0.75 if not latest_test.passed else 0.25
    elif candidate.agent == 'explorer':
        score += 0.85 if decision.action == 'read_more' else 0.4
    elif candidate.agent == 'verifier':
        score += 1.0 if decision.action == 'finish' and latest_test.passed else 0.65 if decision.action == 'read_more' and not latest_test.passed else 0.3
    if candidate.profile == 'conservative' and decision.action == 'read_more':
        score += 1.5
    if candidate.profile == 'memory-aware' and decision.proposal is not None:
        score += 1.0
    if candidate.profile == 'broad-context' and decision.files_to_read:
        score += 0.75
    if candidate.profile == 'test-focused' and any(path.startswith('tests/') for path in decision.files_to_read):
        score += 1.0
    if patch_applied and decision.action == 'patch':
        score -= 1.0
    if any(path.startswith('tests/') for path in decision.files_to_read):
        score += 0.5
    if changed_files and decision.action == 'patch':
        score += 0.25
    if any('upper()' in text for text in discovered.values()) and decision.action == 'patch':
        score += 0.5
    return round(score, 3)



