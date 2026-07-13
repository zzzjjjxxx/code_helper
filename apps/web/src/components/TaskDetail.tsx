import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { ArtifactRecord, MemoryRecord, RetrievalHit, SecurityPolicy, TaskDetail, TaskEvent, TestOutcome } from '../types'
import { BranchComparisonPanel } from './BranchComparisonPanel'
import { CollaborationPanel } from './CollaborationPanel'
import { ArtifactPanel } from './ArtifactPanel'
import { DiffViewer } from './DiffViewer'
import { LogPanel } from './LogPanel'
import { StatusBadge } from './StatusBadge'
import { TaskTimeline } from './TaskTimeline'

interface Props {
  taskId: string
  onRefreshTasks: () => void | Promise<void>
  onSelectTask: (taskId: string) => void
}

function applyEvent(detail: TaskDetail, event: TaskEvent): TaskDetail {
  const next: TaskDetail = { ...detail, events: [...detail.events, event] }

  if (event.type === 'task.started') {
    next.status = 'reading'
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

  if (event.type === 'task.summarized') {
    next.current_step = 'summarize'
    next.summary = String((event.payload as { summary?: unknown }).summary ?? event.message)
  }

  if (event.type === 'task.succeeded') {
    next.status = 'succeeded'
    next.summary = event.message
    next.last_error = null
  }

  if (event.type === 'task.failed') {
    next.status = 'failed'
    next.last_error = String((event.payload as { error?: unknown }).error ?? event.message)
  }

  if (event.type === 'memory.created') {
    const memory = (event.payload as { memory?: MemoryRecord }).memory
    if (memory) {
      next.memory = [memory, ...next.memory]
    }
  }

  if (event.type === 'rollback.completed') {
    next.status = 'rolled_back'
    next.current_step = 'rollback'
    next.summary = event.message
  }

  return next
}

export function TaskDetailPanel({ taskId, onRefreshTasks, onSelectTask }: Props) {
  const [task, setTask] = useState<TaskDetail | null>(null)
  const [artifacts, setArtifacts] = useState<ArtifactRecord[]>([])
  const [policy, setPolicy] = useState<SecurityPolicy | null>(null)
  const [loading, setLoading] = useState(true)
  const [connection, setConnection] = useState<'connecting' | 'live' | 'closed' | 'error'>('connecting')
  const [error, setError] = useState<string | null>(null)
  const [streamSeed, setStreamSeed] = useState(0)

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
      setError(null)
      setConnection('connecting')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load task')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadTask()
  }, [taskId])

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

        if (event.type === 'artifact.created') {
          void loadArtifacts()
        }

        if (event.type === 'task.succeeded' || event.type === 'task.failed' || event.type === 'rollback.completed') {
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

  const handleRun = async () => {
    try {
      await api.runTask(taskId)
      await loadTask()
      await onRefreshTasks()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start the task')
    }
  }

  const handleRollback = async () => {
    try {
      await api.rollbackTask(taskId)
      await loadTask()
      await onRefreshTasks()
      onSelectTask(taskId)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to roll back the task')
    }
  }

  const status = task?.status ?? 'created'
  const isActive = task ? ['queued', 'reading', 'analyzing', 'patching', 'testing', 'awaiting_review'].includes(task.status) : false

  return (
    <div className="detail-layout">
      <div className="detail-header">
        <div>
          <div className="detail-kicker">Selected task</div>
          <h2>{task?.title ?? 'Loading...'}</h2>
          <div className="detail-meta">
            {task ? <StatusBadge status={task.status} /> : null}
            <span className={`connection-pill connection-${connection}`}>{connection}</span>
            <span className="muted">{task?.current_step ?? 'idle'}</span>
          </div>
          {task ? (
            <div className="metric-strip">
              <span className="metric-chip">events {task.events.length}</span>
              <span className="metric-chip">artifacts {artifacts.length}</span>
              <span className="metric-chip">snapshots {task.snapshots.length}</span>
            </div>
          ) : null}
        </div>
        <div className="detail-actions">
          <button className="primary-button" type="button" onClick={() => void handleRun()} disabled={loading || isActive}>
            Run task
          </button>
          <button className="secondary-button" type="button" onClick={() => void handleRollback()} disabled={!task || task.snapshots.length === 0}>
            Roll back
          </button>
        </div>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      {task ? (
        <div className="detail-grid">
          <section className="card">
            <div className="panel-title-row">
              <div>
                <h3>Overview</h3>
                <p className="muted">Repository, focus paths, and run summary.</p>
              </div>
            </div>
            <div className="info-grid">
              <div>
                <span className="muted">Repository</span>
                <br />
                {task.repository_path}
              </div>
              <div>
                <span className="muted">Created</span>
                <br />
                {new Date(task.created_at).toLocaleString()}
              </div>
              <div>
                <span className="muted">Updated</span>
                <br />
                {new Date(task.updated_at).toLocaleString()}
              </div>
              <div>
                <span className="muted">Status</span>
                <br />
                {status}
              </div>
            </div>
            <div className="focus-list">
              <span className="muted">Focus paths</span>
              <ul>
                {task.focus_paths.map((path) => (
                  <li key={path}>{path}</li>
                ))}
              </ul>
            </div>
            {policy ? (
              <div className="security-box">
                <div className="panel-title-row">
                  <div>
                    <h4>Security policy</h4>
                    <p className="muted">Command whitelist and snapshot guardrails.</p>
                  </div>
                </div>
                <div className="info-grid">
                  <div>
                    <span className="muted">Workspace</span>
                    <br />
                    {policy.workspace_root}
                  </div>
                  <div>
                    <span className="muted">Snapshots</span>
                    <br />
                    {policy.snapshot_root}
                  </div>
                </div>
                <div className="metric-strip">
                  <span className="metric-chip">integrity {policy.snapshot_integrity_required ? 'on' : 'off'}</span>
                  <span className="metric-chip">rules {policy.allowed_commands.length}</span>
                </div>
              </div>
            ) : null}
            {task.summary ? <p className="summary-box">{task.summary}</p> : null}
          </section>

          <BranchComparisonPanel events={task.events} />
          <CollaborationPanel events={task.events} />
          <TaskTimeline events={task.events} />
          <ArtifactPanel artifacts={artifacts} />
          <DiffViewer diff={task.latest_diff} />
          <section className="card">
            <div className="panel-title-row">
              <div>
                <h3>Retrieval</h3>
                <p className="muted">Files the agent selected before editing.</p>
              </div>
            </div>
            {task.latest_retrieval.length === 0 ? (
              <div className="empty-subtle">No retrieval hits recorded yet.</div>
            ) : (
              <div className="artifact-list">
                {task.latest_retrieval.map((hit) => (
                  <article key={hit.path} className="timeline-item">
                    <div className="timeline-item-top">
                      <strong>{hit.path}</strong>
                      <span className="muted">{hit.score.toFixed(2)}</span>
                    </div>
                    <p>{hit.reason}</p>
                    <pre className="artifact-content" style={{ maxHeight: '180px' }}>
                      {hit.preview}
                    </pre>
                  </article>
                ))}
              </div>
            )}
          </section>
          <section className="card">
            <div className="panel-title-row">
              <div>
                <h3>Memory</h3>
                <p className="muted">Persisted notes from previous and current runs.</p>
              </div>
            </div>
            {task.memory.length === 0 ? (
              <div className="empty-subtle">No long-term memory saved yet.</div>
            ) : (
              <div className="artifact-list">
                {task.memory.map((memory) => (
                  <article key={memory.memory_id} className="timeline-item">
                    <div className="timeline-item-top">
                      <strong>{memory.title}</strong>
                      <span className="muted">{memory.kind}</span>
                    </div>
                    <p>{memory.content}</p>
                    {memory.related_files.length > 0 ? (
                      <small className="muted">Files: {memory.related_files.join(', ')}</small>
                    ) : null}
                  </article>
                ))}
              </div>
            )}
          </section>
          <LogPanel result={task.latest_test_result} lastError={task.last_error} />
        </div>
      ) : loading ? (
        <div className="empty-state">Loading task...</div>
      ) : (
        <div className="empty-state">Select a task to inspect its workflow.</div>
      )}
    </div>
  )
}
