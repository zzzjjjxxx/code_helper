import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { api } from '../api/client'
import type {
  ArtifactRecord,
  MemoryRecord,
  RetrievalHit,
  SecurityPolicy,
  SubgoalStatus,
  TaskDetail,
  TaskEvent,
  TaskSubgoalRecord,
  TestOutcome,
} from '../types'
import { StatusBadge } from './StatusBadge'

interface Props {
  taskId: string
  onRefreshTasks: () => void | Promise<void>
  onSelectTask: (taskId: string) => void
}

type InspectorKind = 'summary' | 'diff' | 'tests' | 'timeline' | 'retrieval' | 'memory' | 'plan' | 'artifacts' | null

type ChatRole = 'user' | 'assistant'

interface ChatMessage {
  id: string
  role: ChatRole
  title: string
  body: string
  meta: string
  implementationRequest?: boolean
}

interface ProcessEntry {
  id: string
  phase: ProcessPhase
  title: string
  body: string
  meta: string
}

function applyEvent(detail: TaskDetail, event: TaskEvent): TaskDetail {
  const next: TaskDetail = { ...detail, events: [...detail.events, event] }

  const updateSubgoals = (status: SubgoalStatus, subgoalPayload: Partial<TaskSubgoalRecord>) => {
    const subgoalId = subgoalPayload.subgoal_id
    if (!subgoalId) {
      return
    }
    const existing = next.subgoals.find((item) => item.subgoal_id === subgoalId)
    const merged: TaskSubgoalRecord = {
      id: existing?.id ?? null,
      subgoal_id: subgoalId,
      task_id: detail.id,
      position: subgoalPayload.position ?? existing?.position ?? 0,
      phase: subgoalPayload.phase ?? existing?.phase ?? 'analysis',
      title: subgoalPayload.title ?? existing?.title ?? '',
      description: subgoalPayload.description ?? existing?.description ?? '',
      success_criteria: subgoalPayload.success_criteria ?? existing?.success_criteria ?? [],
      files_to_read: subgoalPayload.files_to_read ?? existing?.files_to_read ?? [],
      rationale: subgoalPayload.rationale ?? existing?.rationale ?? '',
      status,
      created_at: existing?.created_at ?? new Date().toISOString(),
      updated_at: new Date().toISOString(),
      completed_at: status === 'completed' ? new Date().toISOString() : existing?.completed_at ?? null,
    }
    const remaining = next.subgoals.filter((item) => item.subgoal_id !== subgoalId)
    next.subgoals = [...remaining, merged].sort((a, b) => a.position - b.position)
  }

  if (event.type === 'task.started') {
    next.status = 'reading'
  }

  if (event.type === 'goal.planned') {
    const subgoals = (event.payload as { subgoals?: TaskSubgoalRecord[] }).subgoals ?? []
    next.subgoals = subgoals
  }

  if (event.type === 'goal.started') {
    const subgoal = (event.payload as { subgoal?: Partial<TaskSubgoalRecord> }).subgoal
    if (subgoal) {
      updateSubgoals('active', subgoal)
    }
  }

  if (event.type === 'goal.completed') {
    const subgoal = (event.payload as { subgoal?: Partial<TaskSubgoalRecord> }).subgoal
    if (subgoal) {
      updateSubgoals('completed', subgoal)
    }
  }

  if (event.type === 'goal.blocked') {
    const subgoal = (event.payload as { subgoal?: Partial<TaskSubgoalRecord> }).subgoal
    if (subgoal) {
      updateSubgoals('blocked', subgoal)
    }
  }

  if (event.type === 'branch.created' || event.type === 'branch.selected' || event.type === 'agent.planner.started' || event.type === 'agent.planner.completed' || event.type === 'replan.requested') {
    next.status = 'analyzing'
    next.current_step = 'analyze'
  }

  if (event.type === 'snapshot.created') {
    next.current_step = 'patch'
  }

  if (event.type === 'patch.applied') {
    next.current_step = 'patch'
    next.latest_diff = String((event.payload as { diff?: unknown }).diff ?? next.latest_diff ?? '')
  }

  if (event.type === 'retrieval.completed') {
    next.latest_retrieval = ((event.payload as { hits?: unknown }).hits ?? []) as RetrievalHit[]
  }

  if (event.type === 'test.completed') {
    next.current_step = 'test'
    next.latest_test_result = event.payload as TestOutcome
  }

  return next
}

function eventToConversationMessage(event: TaskEvent): ChatMessage | null {
  if (event.type === "task.chat.request") {
    const message = typeof event.payload.message === "string" ? event.payload.message : event.message
    return { id: event.event_id, role: "user", title: "You", body: message, meta: "" }
  }
  if (event.type === "task.chat.response") {
    const reply = typeof event.payload.reply === "string" ? event.payload.reply : event.message
    return { id: event.event_id, role: "assistant", title: "Agent", body: reply, meta: "", implementationRequest: event.payload.implementation_request === true }
  }
  return null
}

function buildConversation(task: TaskDetail): ChatMessage[] {
  const messages = task.events.map(eventToConversationMessage).filter((item): item is ChatMessage => item !== null)
  const terminalIndex = task.events.reduce((latest, event, index) => event.type === 'task.succeeded' || event.type === 'task.failed' ? index : latest, -1)
  const latestChatRequestIndex = task.events.reduce((latest, event, index) => event.type === 'task.chat.request' ? index : latest, -1)
  const terminal = terminalIndex >= 0 ? task.events[terminalIndex] : null
  if (terminal && terminalIndex > latestChatRequestIndex && task.status !== 'running' && task.status !== 'queued') {
    const lastImplementation = [...messages].reverse().find((message) => message.role === 'assistant' && message.implementationRequest)
    if (lastImplementation) {
      lastImplementation.body = task.summary || terminal.message
    }
  }
  return messages
}
type ProcessPhase = 'planning' | 'execution' | 'verification' | 'result'

interface ProcessEntry {
  id: string
  phase: ProcessPhase
  title: string
  body: string
  meta: string
}

function classifyProcessPhase(eventType: string): ProcessPhase {
  if (eventType.startsWith('branch.') || eventType.startsWith('goal.') || eventType === 'replan.requested' || eventType.startsWith('agent.planner.')) {
    return 'planning'
  }
  if (eventType === 'test.completed' || eventType.startsWith('command.') || eventType.startsWith('agent.reviewer.')) {
    return 'verification'
  }
  if (eventType === 'task.succeeded' || eventType === 'task.failed' || eventType === 'rollback.completed') {
    return 'result'
  }
  return 'execution'
}

function formatProcessTitle(event: TaskEvent): string {
  if (event.type.startsWith('agent.planner.')) {
    return 'Planner'
  }
  if (event.type.startsWith('agent.executor.')) {
    return 'Executor'
  }
  if (event.type.startsWith('agent.reviewer.')) {
    return 'Reviewer'
  }
  if (event.type.startsWith('goal.')) {
    return 'Plan'
  }
  if (event.type.startsWith('branch.') || event.type === 'replan.requested') {
    return 'Coordinator'
  }
  if (event.type.startsWith('memory.')) {
    return 'Memory'
  }
  if (event.type.startsWith('artifact.')) {
    return 'Artifact'
  }
  if (event.type === 'test.completed') {
    return 'Tests'
  }
  if (event.type === 'task.succeeded' || event.type === 'task.failed' || event.type === 'rollback.completed') {
    return 'Result'
  }
  return 'Agent'
}

function isKeyConversationEvent(event: TaskEvent): boolean {
  return [
    'task.started',
    'retrieval.started',
    'retrieval.completed',
    'goal.planned',
    'goal.started',
    'goal.completed',
    'goal.blocked',
    'snapshot.created',
    'patch.applied',
    'command.started',
    'command.completed',
    'test.completed',
    'agent.planner.started',
    'agent.planner.completed',
    'agent.executor.started',
    'agent.executor.completed',
    'agent.reviewer.started',
    'agent.reviewer.completed',
    'react.observation',
    'branch.comparison.completed',
    'task.succeeded',
    'task.failed',
    'rollback.completed',
    'rollback.failed',
    'replan.requested',
  ].includes(event.type)
}

function eventToProcessEntry(event: TaskEvent): ProcessEntry | null {
  if (!isKeyConversationEvent(event) || event.type === 'task.chat.response' || event.type === 'task.summarized') {
    return null
  }

  const phase = classifyProcessPhase(event.type)
  const meta = new Date(event.created_at).toLocaleTimeString()

  return {
    id: event.event_id,
    phase,
    title: formatProcessTitle(event),
    body: event.message,
    meta,
  }
}

function buildProcessLog(task: TaskDetail): ProcessEntry[] {
  return task.events.map(eventToProcessEntry).filter((item): item is ProcessEntry => item !== null)
}

function formatDuration(durationMs: number | null): string {
  if (durationMs === null || !Number.isFinite(durationMs)) return "in progress"
  if (durationMs < 1000) return `${Math.max(0, Math.round(durationMs))} ms`
  if (durationMs < 60000) return `${(durationMs / 1000).toFixed(1)} s`
  const totalSeconds = Math.floor(durationMs / 1000)
  return `${Math.floor(totalSeconds / 60)} min ${totalSeconds % 60} s`
}

interface ProcessSummary { id: string; title: string; durationMs: number | null }

function buildProcessSummaries(task: TaskDetail): ProcessSummary[] {
  const starts = new Map<string, TaskEvent>()
  const completed: ProcessSummary[] = []
  const hasGoals = task.events.some((event) => event.type === "goal.started" || event.type === "goal.completed")
  if (hasGoals) {
    for (const event of task.events) {
      const subgoal = (event.payload as { subgoal?: Partial<TaskSubgoalRecord> }).subgoal
      const id = subgoal?.subgoal_id
      if (!id) continue
      if (event.type === "goal.started") starts.set(id, event)
      if (event.type === "goal.completed") {
        const started = starts.get(id)
        completed.push({ id, title: subgoal?.title || task.subgoals.find((item) => item.subgoal_id === id)?.title || "Subprocess", durationMs: started ? Math.max(0, new Date(event.created_at).getTime() - new Date(started.created_at).getTime()) : null })
      }
    }
    return completed.slice(-3)
  }
  const has = (types: string[]) => task.events.some((event) => types.includes(event.type))
  const test = task.events.find((event) => event.type === "test.completed")
  return [
    has(["retrieval.completed"]) ? { id: "context", title: "Code context collected", durationMs: null } : null,
    has(["goal.planned", "agent.planner.completed"]) ? { id: "plan", title: "Solution planned", durationMs: null } : null,
    has(["patch.applied"]) ? { id: "changes", title: "Changes implemented", durationMs: null } : null,
    test ? { id: "tests", title: "Tests completed", durationMs: typeof test.payload.duration_ms === "number" ? test.payload.duration_ms : null } : null,
    has(["agent.reviewer.completed"]) ? { id: "review", title: "Review completed", durationMs: null } : null,
  ].filter((item): item is ProcessSummary => item !== null).slice(-3)
}

function taskDuration(task: TaskDetail): number | null {
  const started = task.events.find((event) => event.type === "task.started")
  const finished = [...task.events].reverse().find((event) => event.type === "task.succeeded" || event.type === "task.failed")
  return started && finished ? Math.max(0, new Date(finished.created_at).getTime() - new Date(started.created_at).getTime()) : null
}
function formatWorkingStage(task: TaskDetail): string {
  const step = (task.current_step ?? task.status ?? 'idle').toLowerCase()
  if (step.includes('queue') || step === 'created') {
    return 'queued'
  }
  if (step.includes('read')) {
    return 'reading code'
  }
  if (step.includes('analy')) {
    return 'analyzing'
  }
  if (step.includes('patch')) {
    return 'patching'
  }
  if (step.includes('test')) {
    return 'testing'
  }
  if (step.includes('review')) {
    return 'waiting for review'
  }
  if (step.includes('rollback')) {
    return 'rolling back'
  }
  if (step.includes('succeed')) {
    return 'done'
  }
  if (step.includes('fail')) {
    return 'failed'
  }
  return 'working'
}

function buildProcessBubble(task: TaskDetail, expanded: boolean): ChatMessage {
  const done = ["succeeded", "failed", "rolled_back"].includes(task.status)
  const summaries = buildProcessSummaries(task)
  const lines = summaries.map((item) => "- " + item.title + ": " + formatDuration(item.durationMs))
  const detail = lines.length > 0 ? lines.join("\n") : "No completed subprocesses yet."
  const total = "Total duration: " + formatDuration(taskDuration(task))
  return {
    id: "process-" + task.id,
    role: "assistant",
    title: done ? "Processed" : "Processing - " + formatWorkingStage(task),
    body: expanded ? detail + "\n\n" + total : (done ? "Processing complete. Click to view details." : "Processing your request..."),
    meta: done ? "Completed" : "In progress",
  }
}

function commandFromText(input: string): 'diff' | 'tests' | 'timeline' | 'retrieval' | 'memory' | 'plan' | 'artifacts' | 'summary' | 'run' | 'rollback' | 'help' | null {
  const value = input.trim().toLowerCase()
  if (!value) {
    return null
  }
  if (value.includes('rollback')) {
    return 'rollback'
  }
  if (value.includes('run') || value.includes('execute')) {
    return 'run'
  }
  if (value.includes('diff') || value.includes('patch') || value.includes('change')) {
    return 'diff'
  }
  if (value.includes('test') || value.includes('log') || value.includes('stderr') || value.includes('stdout')) {
    return 'tests'
  }
  if (value.includes('timeline') || value.includes('event')) {
    return 'timeline'
  }
  if (value.includes('retrieval') || value.includes('search') || value.includes('context')) {
    return 'retrieval'
  }
  if (value.includes('memory')) {
    return 'memory'
  }
  if (value.includes('plan') || value.includes('subgoal') || value.includes('goal')) {
    return 'plan'
  }
  if (value.includes('artifact') || value.includes('attachment')) {
    return 'artifacts'
  }
  if (value.includes('summary') || value.includes('overview')) {
    return 'summary'
  }
  return 'help'
}

export function TaskDetailPanel({ taskId, onRefreshTasks, onSelectTask }: Props) {
  const [task, setTask] = useState<TaskDetail | null>(null)
  const [artifacts, setArtifacts] = useState<ArtifactRecord[]>([])
  const [policy, setPolicy] = useState<SecurityPolicy | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [composer, setComposer] = useState('')
  const [loading, setLoading] = useState(true)
  const [connection, setConnection] = useState<'connecting' | 'live' | 'closed' | 'error'>('connecting')
  const [error, setError] = useState<string | null>(null)
  const [streamSeed, setStreamSeed] = useState(0)
  const [inspector, setInspector] = useState<InspectorKind>(null)
  const [showProcess, setShowProcess] = useState(true)
  const transcriptRef = useRef<HTMLDivElement | null>(null)
  const stickToBottomRef = useRef(true)
  const lastMessageCountRef = useRef(0)
  const lastEventCountRef = useRef(0)
  const refreshInFlightRef = useRef(false)

  const loadArtifacts = async () => {
    try {
      const data = await api.listTaskArtifacts(taskId)
      setArtifacts(data)
      return data
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load artifacts')
      return []
    }
  }

  const loadTask = async () => {
    setLoading(true)
    try {
      const [taskData, artifactData, policyData] = await Promise.all([
        api.getTask(taskId),
        api.listTaskArtifacts(taskId),
        api.getSecurityPolicy(),
      ])
      setTask(taskData)
      setArtifacts(artifactData)
      setPolicy(policyData)
      setStreamSeed(taskData.events.length)
      stickToBottomRef.current = true
      setMessages(buildConversation(taskData))
      setShowProcess(true)
      setInspector(null)
      setError(null)
      setConnection('connecting')
      lastMessageCountRef.current = buildConversation(taskData).length
      lastEventCountRef.current = taskData.events.length
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load task')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadTask()
  }, [taskId])

  const processBubble = useMemo(() => (task ? buildProcessBubble(task, showProcess) : null), [task, showProcess])

  useLayoutEffect(() => {
    const transcript = transcriptRef.current
    if (!transcript) {
      return
    }

    const eventCount = task?.events.length ?? 0
    const messageCount = messages.length
    const hasNewContent = messageCount > lastMessageCountRef.current || eventCount > lastEventCountRef.current

    if (hasNewContent && stickToBottomRef.current) {
      transcript.scrollTop = transcript.scrollHeight
    }

    lastMessageCountRef.current = messageCount
    lastEventCountRef.current = eventCount
  }, [messages.length, task?.events.length])

  useEffect(() => {
    if (!task || task.id !== taskId) {
      return
    }

    const source = new EventSource(api.eventStreamUrl(taskId, streamSeed))
    source.onopen = () => setConnection('live')
    source.onerror = () => setConnection('error')
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as TaskEvent
        setTask((current) => (current ? applyEvent(current, event) : current))

        const messageCard = eventToConversationMessage(event)
        if (messageCard) {
          setMessages((current) => [...current, messageCard])
        }

        if (event.type === 'artifact.created') {
          void loadArtifacts()
        }

        if (event.type === 'task.succeeded' || event.type === 'task.failed' || event.type === 'rollback.completed' || event.type === 'rollback.failed') {
          void onRefreshTasks()
          void loadArtifacts()
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to process event')
      }
    }

    return () => {
      setConnection('closed')
      source.close()
    }
  }, [taskId, streamSeed])

  useEffect(() => {
    if (!task) {
      return
    }


    let cancelled = false
    const syncTaskSnapshot = async () => {
      if (refreshInFlightRef.current) {
        return
      }
      refreshInFlightRef.current = true
      try {
        const [taskData, artifactData] = await Promise.all([
          api.getTask(taskId),
          api.listTaskArtifacts(taskId),
        ])
        if (cancelled) {
          return
        }

        setTask((current) => {
          if (current && current.events.length === taskData.events.length && current.updated_at === taskData.updated_at) {
            return current
          }
          return taskData
        })
        setArtifacts(artifactData)
        } catch (err) {
        if (!cancelled) {
          setConnection('error')
          setError(err instanceof Error ? err.message : 'Failed to refresh task status')
        }
      } finally {
        refreshInFlightRef.current = false
      }
    }

    const timer = window.setInterval(() => {
      void syncTaskSnapshot()
    }, 5000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [taskId, task?.id])

  const handleRun = async () => {
    try {
      setMessages((current) => [
        ...current,
        {
          id: `local-run-${Date.now()}`,
          role: 'system',
          title: 'You',
          body: 'Run this task.',
          meta: new Date().toLocaleTimeString(),
        },
      ])
      await api.runTask(taskId)
      await loadTask()
      await onRefreshTasks()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start the task')
    }
  }

  const handleRollback = async () => {
    try {
      setMessages((current) => [
        ...current,
        {
          id: `local-rollback-${Date.now()}`,
          role: 'system',
          title: 'You',
          body: 'Roll back the task.',
          meta: new Date().toLocaleTimeString(),
        },
      ])
      await api.rollbackTask(taskId)
      await loadTask()
      await onRefreshTasks()
      onSelectTask(taskId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to roll back the task')
    }
  }

  const handleSubmit = async (event: { preventDefault(): void }) => {
    event.preventDefault()
    const text = composer.trim()
    if (!text) {
      return
    }

    setMessages((current) => [
      ...current,
      {
        id: `user-${Date.now()}`,
        role: 'user',
        title: 'You',
        body: text,
        meta: new Date().toLocaleTimeString(),
      },
    ])
    setComposer('')

    try {
      const response = await api.chatTask(taskId, { message: text })
      setMessages((current) => [
        ...current,
        {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          title: 'Agent',
          body: response.reply,
          meta: response.follow_up_started ? 'Implementation started' : response.implementation_request ? 'Implementation queued' : 'Reply received',
        },
      ])
      if (response.suggested_panel) {
        setInspector(response.suggested_panel as InspectorKind)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send chat message')
    }
  }

  const inspectorContent = useMemo(() => {
    if (!task || !inspector) {
      return null
    }

    if (inspector === 'summary') {
      return (
        <div className="inspector-grid">
          <div>
            <span className="muted">Repository</span>
            <div>{task.repository_path}</div>
          </div>
          <div>
            <span className="muted">Status</span>
            <div>{task.status}</div>
          </div>
          <div>
            <span className="muted">Current step</span>
            <div>{task.current_step ?? 'idle'}</div>
          </div>
          <div>
            <span className="muted">Updated</span>
            <div>{new Date(task.updated_at).toLocaleString()}</div>
          </div>
        </div>
      )
    }

    if (inspector === 'diff') {
      return task.latest_diff ? <pre className="inspector-pre">{task.latest_diff}</pre> : <div className="empty-subtle">No patch yet.</div>
    }

    if (inspector === 'tests') {
      const result = task.latest_test_result
      return result ? (
        <div className="inspector-stack">
          <div className="inspector-grid">
            <div><span className="muted">Command</span><div>{result.command}</div></div>
            <div><span className="muted">Status</span><div>{result.passed ? 'Passed' : 'Failed'}</div></div>
            <div><span className="muted">Exit code</span><div>{result.return_code}</div></div>
            <div><span className="muted">Duration</span><div>{result.duration_ms} ms</div></div>
          </div>
          <div>
            <span className="muted">stdout</span>
            <pre className="inspector-pre">{result.stdout || 'No stdout'}</pre>
          </div>
          <div>
            <span className="muted">stderr</span>
            <pre className="inspector-pre">{result.stderr || 'No stderr'}</pre>
          </div>
        </div>
      ) : (
        <div className="empty-subtle">No test result yet.</div>
      )
    }

    if (inspector === 'timeline') {
      return (
        <div className="inspector-stack">
          {task.events.length === 0 ? <div className="empty-subtle">Waiting for events...</div> : null}
          {task.events.map((event) => (
            <article key={`${event.sequence}-${event.event_id}`} className="timeline-item">
              <div className="timeline-item-top">
                <span className="timeline-type">
                  #{event.sequence} {event.type}
                </span>
                <span className="timeline-time">{new Date(event.created_at).toLocaleTimeString()}</span>
              </div>
              <p>{event.message}</p>
              <div className="timeline-meta">
                {event.step ? <small className="muted">Step: {event.step}</small> : null}
                <small className="muted">{summarizePayload(event.payload)}</small>
              </div>
            </article>
          ))}
        </div>
      )
    }

    if (inspector === 'retrieval') {
      return task.latest_retrieval.length === 0 ? (
        <div className="empty-subtle">No retrieval hits recorded yet.</div>
      ) : (
        <div className="inspector-stack">
          {task.latest_retrieval.map((hit) => (
            <article key={hit.path} className="timeline-item">
              <div className="timeline-item-top">
                <strong>{hit.path}</strong>
                <span className="muted">{hit.score.toFixed(2)}</span>
              </div>
              <p>{hit.reason}</p>
              <pre className="inspector-pre">{hit.preview}</pre>
            </article>
          ))}
        </div>
      )
    }

    if (inspector === 'memory') {
      return task.memory.length === 0 ? (
        <div className="empty-subtle">No memory notes stored yet.</div>
      ) : (
        <div className="inspector-stack">
          {task.memory.map((memory) => (
            <article key={memory.memory_id} className="timeline-item">
              <div className="timeline-item-top">
                <strong>{memory.title}</strong>
                <span className="muted">{memory.kind}</span>
              </div>
              <p>{memory.content}</p>
              {memory.related_files.length > 0 ? <small className="muted">{memory.related_files.join(', ')}</small> : null}
            </article>
          ))}
        </div>
      )
    }

    if (inspector === 'plan') {
      return task.subgoals.length === 0 ? (
        <div className="empty-subtle">Plan not ready yet.</div>
      ) : (
        <div className="inspector-stack">
          {task.subgoals.map((subgoal) => (
            <article key={subgoal.subgoal_id} className={`subgoal-card subgoal-${subgoal.status}`}>
              <div className="subgoal-topline">
                <strong>{subgoal.position + 1}. {subgoal.title}</strong>
                <span className="subgoal-badge">{subgoal.status}</span>
              </div>
              <div className="muted">{subgoal.phase}</div>
              <p>{subgoal.description}</p>
              {subgoal.success_criteria.length > 0 ? (
                <ul>
                  {subgoal.success_criteria.map((criteria) => (
                    <li key={criteria}>{criteria}</li>
                  ))}
                </ul>
              ) : null}
            </article>
          ))}
        </div>
      )
    }

    if (inspector === 'artifacts') {
      return artifacts.length === 0 ? (
        <div className="empty-subtle">No artifacts yet.</div>
      ) : (
        <div className="artifact-list">
          {artifacts.map((artifact) => (
            <article key={artifact.artifact_id} className="timeline-item">
              <div className="timeline-item-top">
                <strong>{artifact.name}</strong>
                <span className="artifact-type">{artifact.type}</span>
              </div>
              <pre className="inspector-pre">{artifact.content}</pre>
            </article>
          ))}
        </div>
      )
    }

    return null
  }, [artifacts, inspector, task])

  const status = task?.status ?? 'created'
  const isActive = task ? ['queued', 'reading', 'analyzing', 'patching', 'testing', 'awaiting_review'].includes(task.status) : false

  return (
    <div className="detail-layout chat-layout">
      <div className="chat-header">
        <div>
          <div className="detail-kicker">Current task</div>
          <h2 className="chat-title">{task?.title ?? 'Loading...'}</h2>
          <div className="detail-meta compact-meta">
            {task ? <StatusBadge status={task.status} /> : null}
            <span className={`connection-pill connection-${connection}`}>{connection === 'connecting' ? 'connecting' : connection === 'live' ? 'live' : connection === 'closed' ? 'closed' : 'error'}</span>
            <span className="muted">{task?.current_step ?? 'idle'}</span>
          </div>
          {task ? (
            <div className="metric-strip compact-metrics">
              <span className="metric-chip">messages {messages.length}</span>
              <span className="metric-chip">events {task.events.length}</span>
              <span className="metric-chip">artifacts {artifacts.length}</span>
              <span className="metric-chip">subgoals {task.subgoals.length}</span>
            </div>
          ) : null}
        </div>
        <div className="detail-actions chat-actions header-actions">
          <button className="primary-button compact-button" type="button" onClick={() => void handleRun()} disabled={loading || isActive}>
            Run task
          </button>
          <button className="secondary-button compact-button" type="button" onClick={() => void handleRollback()} disabled={!task || task.snapshots.length === 0}>
            Roll back
          </button>
        </div>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      {task ? (
        <>
          <section className="card chat-window">
            <div className="chat-toolbar chat-toolbar-text">
              <div>
                <span className="muted">Conversation</span>
                <div className="chat-toolbar-note">User input stays in chat. The backend shows only the key planning, patching, testing, and review milestones, and the view refreshes automatically while the task is running.</div>
              </div>
            </div>

            <div
              className="chat-transcript"
              ref={transcriptRef}
              onScroll={() => {
                const node = transcriptRef.current
                if (!node) {
                  return
                }
                const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight
                stickToBottomRef.current = distanceFromBottom < 72
              }}
            >
              {messages.map((message) => (
                <article key={message.id} className={`chat-message chat-${message.role}`}>
                  <div className="chat-message-top">
                    <strong>{message.title}</strong>
                  </div>
                  <p>{message.body}</p>
                </article>
              ))}

              {processBubble ? (
                <article className={`chat-message chat-assistant process-message ${processBubble.meta === 'Completed' ? 'process-final' : 'process-live'}`}>
                  <div className="chat-message-top">
                    <strong>{processBubble.title}</strong>
                    <button type="button" className="process-toggle-button" onClick={() => setShowProcess((current) => !current)}>{showProcess ? "Collapse process" : "View process"}</button>
                  </div>
                  <p>{processBubble.body}</p>
                </article>
              ) : null}
            </div>

            <form className="chat-composer" onSubmit={handleSubmit}>
              <textarea
                value={composer}
                onChange={(event) => setComposer(event.target.value)}
                placeholder="Ask me to create files, implement a feature, fix a bug, or show diff, tests, timeline, retrieval, memory, plan, artifacts, run, or rollback..."
                rows={3}
              />
              <div className="chat-composer-footer">
                <div className="chat-hints muted">You can ask me to create files, implement features, fix bugs, or open a panel like diff, tests, timeline, or memory.</div>
                <button className="primary-button" type="submit">Send</button>
              </div>
            </form>
          </section>

          {inspector ? (
            <div className="inspector-backdrop" onClick={() => setInspector(null)}>
            <section className="card inspector-panel" onClick={(event) => event.stopPropagation()}>
              <div className="panel-title-row">
                <div>
                  <h3>{inspector === 'summary' ? 'Summary' : inspector === 'diff' ? 'Diff' : inspector === 'tests' ? 'Tests' : inspector === 'timeline' ? 'Timeline' : inspector === 'retrieval' ? 'Retrieval' : inspector === 'memory' ? 'Memory' : inspector === 'plan' ? 'Plan' : inspector === 'artifacts' ? 'Artifacts' : inspector}</h3>
                  <p className="muted">Open on demand from chat.</p>
                </div>
                <button type="button" className="secondary-button" onClick={() => setInspector(null)}>Close</button>
              </div>
              {policy ? (
                <div className="inspector-policy muted">
                  Workspace {policy.workspace_root} 闂?Snapshots {policy.snapshot_root} 闂?Rules {policy.allowed_commands.length}
                </div>
              ) : null}
              {inspectorContent}
            </section>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}
