from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, TypedDict

from assistant_shared.models import PatchArtifact, RetrievalHit, TaskStep, TestOutcome
from assistant_tools import artifact_from_patch, capture_snapshot, preview_text, read_text_file, replace_once, restore_snapshot, write_text_file

from ..llm import ReActDecision, ReviewDecision
from .parallel_branches import choose_parallel_branch
from .patch_model import PatchProposal


class LangGraphUnavailable(ImportError):
    pass


@dataclass(slots=True)
class AgentGraphRuntime:
    workflow: Any
    emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]]


_runtime_context: ContextVar[AgentGraphRuntime | None] = ContextVar('agent_graph_runtime', default=None)


class AgentGraphState(TypedDict, total=False):
    task_id: str
    task_title: str
    task_description: str
    retrieval_hits: list[RetrievalHit]
    current_context: dict[str, str]
    baseline: TestOutcome
    final_test: TestOutcome
    max_turns: int
    turn: int
    decision: ReActDecision
    proposal: PatchProposal | None
    review: ReviewDecision
    branch_name: str
    branch_history: list[dict[str, object]]
    review_feedback: dict[str, object] | None
    pending_files: list[str]
    patch: PatchArtifact | None
    snapshot_path: Path | None
    changed_files: list[str]
    patch_applied: bool
    llm_used: bool
    llm_model: str | None
    plan_source: str
    review_decision: str
    summary: str
    next_node: str
    execution_error: str
    branch_comparison: dict[str, object]
    require_human_approval: bool
    approval_response: object


class ChatGraphState(TypedDict, total=False):
    context: dict[str, object]
    user_message: str
    response: Any
    review: Any
    route: str


def langgraph_available() -> bool:
    try:
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    return True


async def run_agent_graph(
    *,
    initial_state: AgentGraphState,
    workflow: Any | None = None,
    emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]] | None = None,
    checkpointer: Any | None = None,
    thread_id: str | None = None,
    resume: object | None = None,
) -> AgentGraphState:
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise LangGraphUnavailable('LangGraph is not installed') from exc

    clean_state = dict(initial_state)
    workflow = workflow or clean_state.pop('workflow', None)
    emit = emit or clean_state.pop('emit', None)
    if workflow is None or emit is None:
        raise ValueError('Agent graph runtime requires workflow and emit')
    graph = StateGraph(AgentGraphState)
    graph.add_node('retrieval_context', _context_node)
    graph.add_node('planner', _planner_node)
    graph.add_node('approval_gate', _approval_gate_node)
    graph.add_node('executor', _executor_node)
    graph.add_node('reviewer', _reviewer_node)
    graph.add_node('rollback', _rollback_node)
    graph.add_node('finish', _finish_node)
    graph.add_edge(START, 'retrieval_context')
    graph.add_edge('retrieval_context', 'planner')
    graph.add_conditional_edges('planner', _route, {
        'retrieval_context': 'retrieval_context', 'approval_gate': 'approval_gate',
        'executor': 'executor', 'reviewer': 'reviewer', 'finish': 'finish',
    })
    graph.add_conditional_edges('approval_gate', _route, {'executor': 'executor', 'finish': 'finish'})
    graph.add_conditional_edges('executor', _route, {
        'retrieval_context': 'retrieval_context', 'planner': 'planner', 'reviewer': 'reviewer',
    })
    graph.add_conditional_edges('reviewer', _route, {'finish': 'finish', 'rollback': 'rollback'})
    graph.add_edge('rollback', 'planner')
    graph.add_edge('finish', END)
    compiled = graph.compile(checkpointer=checkpointer)
    config: dict[str, object] = {'recursion_limit': 64}
    if checkpointer is not None:
        config['configurable'] = {'thread_id': thread_id or clean_state['task_id']}
    token = _runtime_context.set(AgentGraphRuntime(workflow=workflow, emit=emit))
    try:
        graph_input: object = clean_state
        if resume is not None:
            from langgraph.types import Command
            graph_input = Command(resume=resume)
        result = await compiled.ainvoke(graph_input, config=config)
    finally:
        _runtime_context.reset(token)
    return AgentGraphState(result)


async def run_chat_graph(*, planner: Any, context: dict[str, object], user_message: str) -> ChatGraphState:
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise LangGraphUnavailable('LangGraph is not installed') from exc

    async def chat_agent(state: ChatGraphState) -> dict[str, object]:
        response = await _to_thread(planner.chat_task, context=state['context'], user_message=state['user_message'])
        return {'response': response}

    async def chat_reviewer(state: ChatGraphState) -> dict[str, object]:
        response = state['response']
        review = await _to_thread(
            planner.review_chat_response,
            context=state['context'], user_message=state['user_message'], assistant_reply=response.reply,
            implementation_request=response.implementation_request,
        )
        return {'review': review}

    async def implementation_route(_: ChatGraphState) -> dict[str, object]:
        return {'route': 'implementation'}

    async def panel_route(_: ChatGraphState) -> dict[str, object]:
        return {'route': 'panel'}

    async def informational_route(state: ChatGraphState) -> dict[str, object]:
        return {'route': state['response'].intent or 'unknown'}

    def route_intent(state: ChatGraphState) -> str:
        intent = state['response'].intent
        if state['response'].implementation_request or intent == 'implementation':
            return 'implementation'
        if intent == 'panel':
            return 'panel'
        return 'informational'

    graph = StateGraph(ChatGraphState)
    graph.add_node('chat_agent', chat_agent)
    graph.add_node('implementation_route', implementation_route)
    graph.add_node('panel_route', panel_route)
    graph.add_node('informational_route', informational_route)
    graph.add_node('chat_reviewer', chat_reviewer)
    graph.add_edge(START, 'chat_agent')
    graph.add_conditional_edges('chat_agent', route_intent, {
        'implementation': 'implementation_route', 'panel': 'panel_route', 'informational': 'informational_route',
    })
    graph.add_edge('implementation_route', 'chat_reviewer')
    graph.add_edge('panel_route', 'chat_reviewer')
    graph.add_edge('informational_route', 'chat_reviewer')
    graph.add_edge('chat_reviewer', END)
    return ChatGraphState(await graph.compile().ainvoke({'context': context, 'user_message': user_message}))


def _runtime(state: AgentGraphState | None = None) -> AgentGraphRuntime:
    runtime = _runtime_context.get()
    if runtime is None and state is not None:
        workflow = state.get('workflow')
        emit = state.get('emit')
        if workflow is not None and emit is not None:
            return AgentGraphRuntime(workflow=workflow, emit=emit)
    if runtime is None:
        raise RuntimeError('Agent graph node executed without runtime context')
    return runtime


def _route(state: AgentGraphState) -> str:
    return state.get('next_node', 'finish')


async def _context_node(state: AgentGraphState) -> dict[str, object]:
    runtime = _runtime(state)
    current_context = dict(state.get('current_context', {}))
    newly_read = runtime.workflow._read_requested_files(state.get('pending_files', []), current_context)
    current_context.update(newly_read)
    for path, content in newly_read.items():
        await runtime.emit('file.read', TaskStep.read, f'Read {path}', {'path': path, 'preview': preview_text(content)})
    return {'current_context': current_context, 'pending_files': [], 'next_node': 'planner'}


async def _planner_node(state: AgentGraphState) -> dict[str, object]:
    runtime = _runtime(state)
    workflow = runtime.workflow
    emit = runtime.emit
    turn = state.get('turn', 0) + 1
    if turn > state.get('max_turns', 4):
        return {'turn': turn - 1, 'next_node': 'finish', 'review_decision': 'exhausted'}
    selection = await choose_parallel_branch(
        workflow, turn=turn, task_title=state['task_title'], task_description=state['task_description'],
        retrieval_hits=state['retrieval_hits'], discovered=state['current_context'], baseline=state['baseline'],
        latest_test=state['final_test'], patch_applied=state.get('patch_applied', False),
        changed_files=state.get('changed_files', []), review_feedback=state.get('review_feedback'),
        branch_history=state.get('branch_history', []), emit=emit,
    )
    decision = selection.decision
    branch_name = selection.branch_id
    history = list(state.get('branch_history', []))
    history.append({
        'turn': turn, 'branch': branch_name, 'agent': selection.agent, 'profile': selection.profile,
        'action': decision.action, 'summary': decision.summary, 'rationale': decision.rationale,
        'files_to_read': decision.files_to_read, 'score': selection.score, 'selected': True,
        'candidates': [{
            'branch': candidate.branch_id, 'agent': candidate.agent, 'profile': candidate.profile,
            'action': candidate.decision.action, 'summary': candidate.decision.summary,
            'rationale': candidate.decision.rationale, 'files_to_read': candidate.decision.files_to_read,
            'score': candidate.score, 'selected': candidate.selected,
        } for candidate in selection.candidates],
    })
    await emit('agent.planner.completed', TaskStep.analyze, decision.summary or 'Planner chose the current branch.', {
        'agent': selection.agent, 'agent_profile': selection.profile, 'branch': branch_name,
        'selected_branch': branch_name, 'action': decision.action, 'rationale': decision.rationale,
        'files_to_read': decision.files_to_read, 'score': selection.score,
    })
    update: dict[str, object] = {
        'turn': turn, 'decision': decision, 'branch_name': branch_name, 'branch_history': history,
        'llm_used': state.get('llm_used', False) or any(item.decision.provider == 'openai' for item in selection.candidates),
        'proposal': None, 'execution_error': '',
    }
    if decision.provider and decision.model:
        update.update(llm_model=decision.model, plan_source=f'{decision.provider}:{decision.model}')
    if decision.action == 'read_more':
        update.update(next_node='retrieval_context', pending_files=decision.files_to_read, review_feedback={
            'action': 'revise', 'branch': branch_name, 'summary': decision.summary,
            'rationale': decision.rationale, 'files_to_read': decision.files_to_read,
        })
    elif decision.action == 'patch':
        proposal = workflow._resolve_react_proposal(decision, state['current_context'], state['final_test'])
        if proposal is None:
            await emit('llm.plan.invalid', TaskStep.analyze, 'No usable patch proposal was returned', {
                'branch': branch_name, 'action': decision.action, 'rationale': decision.rationale,
            })
            update.update(next_node='planner', review_feedback={
                'action': 'revise', 'branch': branch_name, 'summary': 'No usable patch proposal was returned.',
                'rationale': decision.rationale,
            })
        else:
            update.update(
                proposal=proposal,
                next_node='approval_gate' if state.get('require_human_approval', False) else 'executor',
            )
    else:
        update.update(next_node='reviewer')
    return update


async def _approval_gate_node(state: AgentGraphState) -> dict[str, object]:
    from langgraph.types import interrupt

    proposal = state.get('proposal')
    response = interrupt({
        'type': 'patch.approval.required',
        'task_id': state['task_id'],
        'branch': state.get('branch_name'),
        'path': proposal.path if proposal is not None else None,
        'operation': proposal.operation if proposal is not None else None,
        'summary': state.get('decision').summary if state.get('decision') is not None else '',
    })
    approved = response is True or isinstance(response, dict) and response.get('approved') is True
    if approved:
        return {'approval_response': response, 'next_node': 'executor'}
    return {
        'approval_response': response,
        'review_decision': 'reject',
        'summary': 'The proposed patch was not approved, so no workspace change was applied.',
        'next_node': 'finish',
    }

async def _executor_node(state: AgentGraphState) -> dict[str, object]:
    runtime = _runtime(state)
    workflow = runtime.workflow
    emit = runtime.emit
    proposal = state.get('proposal')
    branch_name = state['branch_name']
    tool_call = await _to_thread(workflow.llm_planner.plan_executor_tool, state={
        'task_title': state['task_title'], 'task_description': state['task_description'],
        'current_context': state['current_context'],
        'proposal': None if proposal is None else {
            'operation': proposal.operation, 'path': proposal.path, 'old': proposal.old, 'new': proposal.new,
        },
        'latest_test': state['final_test'].model_dump(mode='json'),
        'changed_files': state.get('changed_files', []), 'patch_applied': state.get('patch_applied', False),
    })
    tool = tool_call.tool if tool_call is not None else ('write_file' if proposal is not None else 'finish')
    if tool_call is not None:
        await emit('agent.executor.tool.selected', TaskStep.patch, f'Executor selected {tool}', {
            'agent': 'executor', 'tool': tool, 'path': tool_call.path, 'summary': tool_call.summary,
            'provider': tool_call.provider, 'model': tool_call.model,
        })
    if tool == 'read_file':
        return {'pending_files': [tool_call.path], 'next_node': 'retrieval_context', 'review_feedback': {
            'action': 'revise', 'branch': branch_name, 'summary': tool_call.summary, 'files_to_read': [tool_call.path],
        }}
    if tool == 'finish':
        return {'next_node': 'reviewer'}
    if tool == 'run_tests':
        if not state.get('patch_applied'):
            return {'next_node': 'planner', 'review_feedback': {'action': 'revise', 'summary': 'Tests require an applied patch.'}}
        return {'final_test': await _run_and_emit_tests(state), 'next_node': 'reviewer'}
    if proposal is None:
        return {'next_node': 'planner', 'review_feedback': {'action': 'revise', 'summary': 'No writable patch was provided.'}}
    if tool_call is not None:
        proposal = PatchProposal(path=tool_call.path, old=tool_call.old, new=tool_call.content, operation=proposal.operation)

    snapshot_path = workflow.snapshot_root / state['task_id'] / branch_name / 'before_patch'
    capture_snapshot(workflow.workspace_root, snapshot_path)
    await emit('snapshot.created', TaskStep.patch, 'Captured a rollback snapshot', {'snapshot_path': str(snapshot_path), 'branch': branch_name})
    await emit('agent.executor.started', TaskStep.patch, f'Executor is applying {branch_name}', {
        'agent': 'executor', 'branch': branch_name, 'proposal': {'path': proposal.path, 'old': proposal.old, 'new': proposal.new},
    })
    try:
        target = workflow._resolve_workspace_path(proposal.path)
        if target is None:
            raise ValueError(f'Unable to resolve proposal path: {proposal.path}')
        relative_path = target.relative_to(workflow.workspace_root).as_posix()
        if proposal.operation == 'create':
            if target.exists():
                raise ValueError(f'Cannot create existing file: {relative_path}')
            before, after = '', proposal.new
        else:
            if not target.exists():
                raise ValueError(f'Cannot modify missing file: {relative_path}')
            before = read_text_file(target)
            after = replace_once(before, proposal.old, proposal.new)
        write_text_file(target, after)
        patch = artifact_from_patch(target, before, after)
        context = dict(state['current_context'])
        context[relative_path] = after
        await emit('patch.applied', TaskStep.patch, f'Patched {proposal.path}', {
            'path': proposal.path, 'diff': patch.diff, 'rationale': state['decision'].rationale, 'branch': branch_name,
        })
        final_test = await _run_and_emit_tests(state)
        await emit('agent.executor.completed', TaskStep.test, f'Executor finished {branch_name}', {
            'agent': 'executor', 'branch': branch_name, 'passed': final_test.passed,
            'patch_applied': True, 'changed_files': [relative_path],
        })
        return {
            'proposal': proposal, 'snapshot_path': snapshot_path, 'patch': patch, 'patch_applied': True,
            'changed_files': [relative_path], 'current_context': context, 'final_test': final_test, 'next_node': 'reviewer',
        }
    except Exception as exc:
        await emit('agent.executor.completed', TaskStep.patch, f'Executor failed {branch_name}', {
            'agent': 'executor', 'branch': branch_name, 'passed': False, 'patch_applied': False, 'error': str(exc),
        })
        return {'snapshot_path': snapshot_path, 'execution_error': str(exc), 'next_node': 'reviewer'}


async def _run_and_emit_tests(state: AgentGraphState) -> TestOutcome:
    runtime = _runtime(state)
    outcome = await runtime.workflow._run_tests(emit=runtime.emit, step=TaskStep.test, label=f"branch-{state.get('turn', 0)}")
    await runtime.emit('test.completed', TaskStep.test, 'Ran the verification test suite', {
        'passed': outcome.passed, 'return_code': outcome.return_code, 'stdout': outcome.stdout,
        'stderr': outcome.stderr, 'command': outcome.command, 'duration_ms': outcome.duration_ms,
        'branch': state.get('branch_name'),
    })
    return outcome


async def _reviewer_node(state: AgentGraphState) -> dict[str, object]:
    runtime = _runtime(state)
    workflow = runtime.workflow
    emit = runtime.emit
    if state.get('execution_error'):
        review = ReviewDecision(action='reject', summary='The executor could not safely apply the patch.',
                                rationale=state['execution_error'], provider='runtime', model='runtime')
        await emit('agent.reviewer.started', TaskStep.review, 'Reviewer is checking the failed branch', {'agent': 'reviewer', 'branch': state['branch_name']})
        await emit('agent.reviewer.completed', TaskStep.review, review.summary, {
            'agent': 'reviewer', 'branch': state['branch_name'], 'action': review.action,
            'rationale': review.rationale, 'summary': review.summary,
        })
    else:
        review = await workflow._review_react_decision(
            branch_name=state['branch_name'], task_title=state['task_title'], task_description=state['task_description'],
            decision=state['decision'], proposal=state.get('proposal'), final_test=state['final_test'],
            changed_files=state.get('changed_files', []), retrieval_hits=state['retrieval_hits'],
            current_context=state['current_context'], baseline=state['baseline'], branch_history=state.get('branch_history', []), emit=emit,
        )
    if review.action == 'approve' and not state['final_test'].passed:
        review = ReviewDecision(action='revise', summary=review.summary or 'Verification did not pass.',
                                rationale='Approval requires confirmed verification results.', files_to_read=review.files_to_read,
                                provider=review.provider, model=review.model)
        await emit('agent.reviewer.completed', TaskStep.review, review.summary, {
            'agent': 'reviewer', 'branch': state['branch_name'], 'action': review.action,
            'rationale': review.rationale, 'summary': review.summary, 'files_to_read': review.files_to_read,
        })
    if review.action in {'revise', 'reject'}:
        await emit('replan.requested', TaskStep.analyze, 'Reviewer requested a new branch', {
            'branch': state['branch_name'], 'action': review.action, 'rationale': review.rationale,
            'summary': review.summary, 'files_to_read': review.files_to_read,
        })
        return {'review': review, 'review_decision': review.action, 'next_node': 'rollback'}
    summary = review.summary or state['decision'].summary or workflow._summarize_outcome(state['final_test'], state.get('changed_files', []))
    await emit('react.observation', TaskStep.review, summary, {
        'action': 'approve', 'branch': state['branch_name'], 'rationale': review.rationale, 'summary': review.summary,
    })
    return {'review': review, 'review_decision': 'approve', 'summary': summary, 'next_node': 'finish'}


async def _rollback_node(state: AgentGraphState) -> dict[str, object]:
    runtime = _runtime(state)
    workflow = runtime.workflow
    snapshot_path = state.get('snapshot_path')
    if snapshot_path is not None:
        restore_snapshot(snapshot_path, workflow.workspace_root)
        await runtime.emit('branch.reverted', TaskStep.rollback, f"Restored the workspace after {state['branch_name']}", {
            'branch': state['branch_name'], 'snapshot_path': str(snapshot_path),
        })
    review = state['review']
    context = workflow._read_context_files(state['retrieval_hits'], workflow.memory_notes)
    context.update(workflow._read_requested_files(review.files_to_read, context))
    return {
        'current_context': context,
        'review_feedback': {'action': review.action, 'branch': state['branch_name'], 'summary': review.summary,
                            'rationale': review.rationale, 'files_to_read': review.files_to_read},
        'patch': None, 'proposal': None, 'snapshot_path': None, 'patch_applied': False, 'changed_files': [],
        'final_test': state['baseline'], 'execution_error': '', 'next_node': 'planner',
    }


async def _finish_node(state: AgentGraphState) -> dict[str, object]:
    runtime = _runtime(state)
    workflow = runtime.workflow
    summary = state.get('summary') or workflow._summarize_outcome(state['final_test'], state.get('changed_files', []))
    comparison = workflow._build_branch_comparison(
        branch_history=state.get('branch_history', []), summary=summary, final_test=state['final_test'],
        changed_files=state.get('changed_files', []), review_decision=state.get('review_decision', 'pending'),
        llm_used=state.get('llm_used', False),
    )
    await runtime.emit('branch.comparison.completed', TaskStep.summarize, comparison.get('summary', summary), comparison)
    return {'summary': summary, 'branch_comparison': comparison, 'next_node': 'finish'}


class SubgoalGraphState(TypedDict, total=False):
    baseline: Any
    react: Any
    summary: str


async def run_subgoal_graph(
    *,
    task_id: str,
    goals: list[Any],
    emit: Callable[[str, TaskStep | str, str, dict | None], Awaitable[None]],
    serialize_goal: Callable[..., dict[str, object]],
    goal_step: Callable[[Any], TaskStep],
    inspect: Callable[[], Awaitable[Any]],
    implement: Callable[[Any], Awaitable[Any]],
    verify: Callable[[Any], Awaitable[str]],
) -> SubgoalGraphState:
    async def start_goal(index: int) -> None:
        if index >= len(goals):
            return
        goal = goals[index]
        await emit(
            'goal.started', goal_step(goal), f'Started goal: {goal.title}',
            {'subgoal': serialize_goal(task_id, goal, status='active')},
        )

    async def complete_goal(index: int) -> None:
        if index >= len(goals):
            return
        goal = goals[index]
        await emit(
            'goal.completed', goal_step(goal), f'Completed goal: {goal.title}',
            {'subgoal': serialize_goal(task_id, goal, status='completed')},
        )

    async def inspect_node(_: SubgoalGraphState) -> dict[str, object]:
        await start_goal(0)
        baseline = await inspect()
        await complete_goal(0)
        return {'baseline': baseline}

    async def implement_node(state: SubgoalGraphState) -> dict[str, object]:
        await start_goal(1)
        react = await implement(state['baseline'])
        await complete_goal(1)
        return {'react': react}

    async def verify_node(state: SubgoalGraphState) -> dict[str, object]:
        await start_goal(2)
        summary = await verify(state['react'])
        await complete_goal(2)
        return {'summary': summary}

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        state: SubgoalGraphState = {}
        state.update(await inspect_node(state))
        state.update(await implement_node(state))
        state.update(await verify_node(state))
        return state

    graph = StateGraph(SubgoalGraphState)
    graph.add_node('inspect', inspect_node)
    graph.add_node('implement', implement_node)
    graph.add_node('verify', verify_node)
    graph.add_edge(START, 'inspect')
    graph.add_edge('inspect', 'implement')
    graph.add_edge('implement', 'verify')
    graph.add_edge('verify', END)
    return SubgoalGraphState(await graph.compile().ainvoke({}))

class TaskLifecycleState(TypedDict, total=False):
    prepared: bool
    result: Any
    persisted: bool


async def run_task_lifecycle_graph(
    *,
    prepare: Callable[[], Awaitable[None]],
    execute: Callable[[], Awaitable[Any]],
    persist: Callable[[Any], Awaitable[None]],
) -> TaskLifecycleState:
    async def prepare_node(_: TaskLifecycleState) -> dict[str, object]:
        await prepare()
        return {'prepared': True}

    async def execute_node(_: TaskLifecycleState) -> dict[str, object]:
        return {'result': await execute()}

    async def persist_node(state: TaskLifecycleState) -> dict[str, object]:
        await persist(state['result'])
        return {'persisted': True}

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        state: TaskLifecycleState = {}
        state.update(await prepare_node(state))
        state.update(await execute_node(state))
        state.update(await persist_node(state))
        return state

    graph = StateGraph(TaskLifecycleState)
    graph.add_node('prepare', prepare_node)
    graph.add_node('execute', execute_node)
    graph.add_node('persist', persist_node)
    graph.add_edge(START, 'prepare')
    graph.add_edge('prepare', 'execute')
    graph.add_edge('execute', 'persist')
    graph.add_edge('persist', END)
    return TaskLifecycleState(await graph.compile().ainvoke({}))

async def _to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    import asyncio
    return await asyncio.to_thread(func, *args, **kwargs)
