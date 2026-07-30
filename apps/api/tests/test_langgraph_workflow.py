from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from assistant_shared.models import TaskStep, TestOutcome as Outcome
from agent_core.llm import ChatResponseResult, ChatResponseReviewResult, ReActDecision, ReviewDecision, TaskSubgoalSpec
from agent_core.workflow.langgraph_workflow import _executor_node, _reviewer_node, _rollback_node
from agent_core.workflow.langgraph_workflow import LangGraphUnavailable, langgraph_available, run_agent_graph, run_chat_graph, run_subgoal_graph, run_task_lifecycle_graph
from agent_core.workflow.patch_model import PatchProposal
from agent_core.workflow.pipeline import CodeTaskWorkflow, ReactLoopResult


async def _emit(event_type: str, step: TaskStep | str, message: str, payload: dict | None = None) -> None:
    return None


def _outcome(*, passed: bool) -> Outcome:
    return Outcome(command='pytest', return_code=0 if passed else 1, passed=passed)


class _Planner:
    def plan_executor_tool(self, **_):
        return None


class _NodeWorkflow:
    def __init__(self, workspace: Path, snapshot_root: Path, *, review: ReviewDecision, tests_passed: bool = True):
        self.workspace_root = workspace
        self.snapshot_root = snapshot_root
        self.memory_notes = []
        self.llm_planner = _Planner()
        self.review = review
        self.tests_passed = tests_passed

    def _resolve_workspace_path(self, path: str) -> Path | None:
        target = (self.workspace_root / path).resolve()
        return target if target.is_relative_to(self.workspace_root.resolve()) else None

    async def _run_tests(self, **_) -> Outcome:
        return _outcome(passed=self.tests_passed)

    async def _review_react_decision(self, **_) -> ReviewDecision:
        return self.review

    def _summarize_outcome(self, final_test: Outcome, changed_files: list[str]) -> str:
        return 'verified' if final_test.passed else 'not verified'

    def _read_context_files(self, *_):
        return {}

    def _read_requested_files(self, *_):
        return {}

    def _build_branch_comparison(self, **kwargs):
        return {'summary': kwargs['summary'], 'review_decision': kwargs['review_decision']}


def _base_state(workflow: _NodeWorkflow, *, passed: bool = True) -> dict:
    return {
        'workflow': workflow,
        'emit': _emit,
        'task_id': 'task-1',
        'task_title': 'Change code',
        'task_description': 'Apply the requested implementation',
        'retrieval_hits': [],
        'current_context': {},
        'baseline': _outcome(passed=True),
        'final_test': _outcome(passed=passed),
        'decision': ReActDecision(action='patch', summary='done'),
        'branch_name': 'turn-1-planner',
        'branch_history': [],
        'changed_files': [],
    }


@pytest.mark.parametrize('operation', ['create', 'modify'])
def test_executor_uses_safe_write_path_for_create_and_modify(tmp_path: Path, operation: str) -> None:
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    target = workspace / 'module.py'
    old = ''
    if operation == 'modify':
        old = 'value = 1\n'
        target.write_text(old, encoding='utf-8')
    proposal = PatchProposal(path='module.py', old=old, new='value = 2\n', operation=operation)
    workflow = _NodeWorkflow(workspace, tmp_path / 'snapshots', review=ReviewDecision(action='approve'))
    state = {**_base_state(workflow), 'proposal': proposal}

    result = asyncio.run(_executor_node(state))

    assert target.read_text(encoding='utf-8') == 'value = 2\n'
    assert result['changed_files'] == ['module.py']
    assert result['patch_applied'] is True
    assert result['next_node'] == 'reviewer'


def test_reviewer_approve_finishes_and_failed_test_revises(tmp_path: Path) -> None:
    workflow = _NodeWorkflow(tmp_path, tmp_path / 'snapshots', review=ReviewDecision(action='approve', summary='approved'))
    approved = asyncio.run(_reviewer_node({**_base_state(workflow), 'changed_files': ['module.py']}))
    failed = asyncio.run(_reviewer_node({**_base_state(workflow, passed=False), 'changed_files': ['module.py']}))

    assert approved['next_node'] == 'finish'
    assert approved['review_decision'] == 'approve'
    assert failed['next_node'] == 'rollback'
    assert failed['review_decision'] == 'revise'


@pytest.mark.parametrize('action', ['revise', 'reject'])
def test_reviewer_rejection_rolls_back_and_keeps_feedback(tmp_path: Path, action: str) -> None:
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    target = workspace / 'module.py'
    target.write_text('before\n', encoding='utf-8')
    workflow = _NodeWorkflow(
        workspace,
        tmp_path / 'snapshots',
        review=ReviewDecision(action=action, summary='change it', rationale='review evidence'),
    )
    proposal = PatchProposal(path='module.py', old='before\n', new='after\n', operation='modify')
    executed = asyncio.run(_executor_node({**_base_state(workflow), 'proposal': proposal}))
    reviewed = asyncio.run(_reviewer_node({**_base_state(workflow), **executed, 'decision': ReActDecision(action='patch')}))
    rolled_back = asyncio.run(_rollback_node({**_base_state(workflow), **executed, **reviewed}))

    assert target.read_text(encoding='utf-8') == 'before\n'
    assert rolled_back['changed_files'] == []
    assert rolled_back['patch_applied'] is False
    assert rolled_back['review_feedback']['action'] == action


def test_pipeline_falls_back_when_langgraph_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = CodeTaskWorkflow(tmp_path)
    expected = ReactLoopResult(
        summary='fallback', patch=None, final_test=_outcome(passed=True), snapshot_path=None,
        changed_files=[], llm_used=False, plan_source='heuristic', llm_model=None,
        react_turns=1, branch_count=1, review_decision='approve', subgoal_count=0, branch_comparison={},
    )

    async def unavailable(**_):
        raise LangGraphUnavailable('missing')

    async def fallback(**_):
        return expected

    import agent_core.workflow.langgraph_workflow as graph_module
    monkeypatch.setattr(graph_module, 'run_agent_graph', unavailable)
    monkeypatch.setattr(workflow, '_run_collaborative_loop_fallback', fallback)
    result = asyncio.run(workflow._run_collaborative_loop(
        task_id='task-1', task_title='title', task_description='description',
        retrieval_hits=[], discovered={}, baseline=_outcome(passed=True), emit=_emit,
    ))
    assert result is expected


@pytest.mark.skipif(not langgraph_available(), reason='LangGraph optional dependency is not installed')
def test_chat_subgraph_keeps_multi_turn_messages_separate() -> None:
    class Planner:
        def chat_task(self, *, context, user_message):
            return ChatResponseResult(
                reply=f'reply:{user_message}',
                suggested_panel='summary',
                intent=user_message,
                implementation_request=False,
                model='test',
                provider='heuristic',
            )

        def review_chat_response(self, **_):
            return ChatResponseReviewResult(
                adequate=True,
                corrected_reply=None,
                reason='confirmed',
                suggested_panel=None,
                model='test',
                provider='heuristic',
            )

    planner = Planner()
    first = asyncio.run(run_chat_graph(planner=planner, context={'recent_events': []}, user_message='first'))
    second = asyncio.run(run_chat_graph(planner=planner, context={'recent_events': []}, user_message='second'))

    assert first['response'].reply == 'reply:first'
    assert second['response'].reply == 'reply:second'
    assert first['route'] == 'first'
    assert second['route'] == 'second'

@pytest.mark.skipif(not langgraph_available(), reason='LangGraph optional dependency is not installed')
def test_compiled_agent_graph_approve_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from langgraph.checkpoint.memory import InMemorySaver
    from agent_core.workflow.parallel_branches import BranchCandidate, ParallelBranchSelection
    import agent_core.workflow.langgraph_workflow as runtime

    decision = ReActDecision(action='finish', summary='already complete', provider='heuristic', model='heuristic')
    candidate = BranchCandidate(
        branch_id='turn-1-planner',
        agent='planner',
        profile='balanced',
        decision=decision,
        score=1.0,
        selected=True,
    )

    async def choose(*_, **__):
        return ParallelBranchSelection(
            branch_id=candidate.branch_id,
            agent=candidate.agent,
            profile=candidate.profile,
            decision=decision,
            score=1.0,
            candidates=[candidate],
        )

    monkeypatch.setattr(runtime, 'choose_parallel_branch', choose)
    workflow = _NodeWorkflow(
        tmp_path,
        tmp_path / 'snapshots',
        review=ReviewDecision(action='approve', summary='confirmed'),
    )
    result = asyncio.run(run_agent_graph(checkpointer=InMemorySaver(), thread_id='task-1', initial_state={
        **_base_state(workflow),
        'max_turns': 2,
        'turn': 0,
        'pending_files': [],
        'patch_applied': False,
        'llm_used': False,
        'plan_source': 'heuristic',
        'review_decision': 'pending',
    }))

    assert result['review_decision'] == 'approve'
    assert result['summary'] == 'confirmed'
    assert result['branch_comparison']['review_decision'] == 'approve'
    assert 'workflow' not in result
    assert 'emit' not in result

def test_subgoal_graph_runs_inspect_implement_verify_in_order() -> None:
    events: list[str] = []
    goals = [
        TaskSubgoalSpec(position=1, phase='inspect', title='Inspect'),
        TaskSubgoalSpec(position=2, phase='implement', title='Implement'),
        TaskSubgoalSpec(position=3, phase='verify', title='Verify'),
    ]

    async def emit(event_type, *_):
        events.append(event_type)

    async def inspect():
        events.append('inspect.node')
        return _outcome(passed=True)

    async def implement(baseline):
        assert baseline.passed is True
        events.append('implement.node')
        return 'implementation-result'

    async def verify(result):
        assert result == 'implementation-result'
        events.append('verify.node')
        return 'summary'

    state = asyncio.run(run_subgoal_graph(
        task_id='task-1', goals=goals, emit=emit,
        serialize_goal=lambda task_id, goal, *, status: {'task_id': task_id, 'status': status},
        goal_step=lambda _: TaskStep.analyze,
        inspect=inspect, implement=implement, verify=verify,
    ))
    assert state['summary'] == 'summary'
    assert events == [
        'goal.started', 'inspect.node', 'goal.completed',
        'goal.started', 'implement.node', 'goal.completed',
        'goal.started', 'verify.node', 'goal.completed',
    ]


def test_task_lifecycle_graph_runs_prepare_execute_persist_in_order() -> None:
    calls: list[str] = []

    async def prepare():
        calls.append('prepare')

    async def execute():
        calls.append('execute')
        return {'confirmed': True}

    async def persist(result):
        assert result == {'confirmed': True}
        calls.append('persist')

    state = asyncio.run(run_task_lifecycle_graph(prepare=prepare, execute=execute, persist=persist))
    assert calls == ['prepare', 'execute', 'persist']
    assert state['persisted'] is True


@pytest.mark.skipif(not langgraph_available(), reason='LangGraph optional dependency is not installed')
def test_parallel_branch_graph_uses_send_for_all_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_core.workflow import parallel_branches
    from agent_core.workflow.parallel_branches import BranchCandidate

    events: list[tuple[str, dict]] = []
    workflow = _NodeWorkflow(tmp_path, tmp_path / 'snapshots', review=ReviewDecision(action='approve'))

    async def build_candidate(_workflow, *, spec, **_):
        return BranchCandidate(
            branch_id=f"turn-1-{spec['agent']}", agent=spec['agent'], profile=spec['profile'],
            decision=ReActDecision(action='finish', summary=spec['agent'], provider='heuristic'), score=0.0,
        )

    async def emit(event_type, _step, _message, payload=None):
        events.append((event_type, payload or {}))

    monkeypatch.setattr(parallel_branches, '_build_candidate', build_candidate)
    result = asyncio.run(parallel_branches.choose_parallel_branch(
        workflow, turn=1, task_title='title', task_description='description', retrieval_hits=[],
        discovered={}, baseline=_outcome(passed=True), latest_test=_outcome(passed=True),
        patch_applied=False, changed_files=[], review_feedback=None, branch_history=[], emit=emit,
    ))
    assert len(result.candidates) == 5
    assert {candidate.agent for candidate in result.candidates} == {'planner', 'critic', 'memory', 'explorer', 'verifier'}
    assert [event for event, _ in events].count('branch.selected') == 1