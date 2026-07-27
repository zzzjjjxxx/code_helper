import { StatusBadge } from './StatusBadge'
import type { TaskSummary } from '../types'

interface Props {
  tasks: TaskSummary[]
  selectedTaskId: string | null
  loading: boolean
  onSelect: (taskId: string) => void
  onDelete: (taskId: string) => void
}

type WorkspaceGroup = {
  key: string
  label: string
  detail: string | null
  tasks: TaskSummary[]
}

function normalizePath(path: string): string {
  return path.replace(/\\/g, '/').replace(/\/+$/, '')
}

function getWorkspaceLabel(path: string): string {
  const normalized = normalizePath(path)
  const segments = normalized.split('/').filter(Boolean)
  return segments[segments.length - 1] || normalized || 'Workspace'
}

function getWorkspaceDetail(path: string): string {
  const normalized = normalizePath(path)
  const segments = normalized.split('/').filter(Boolean)
  if (segments.length <= 1) {
    return normalized || 'Workspace'
  }
  return normalized
}

function isActiveStatus(status: TaskSummary['status']): boolean {
  return ['queued', 'reading', 'analyzing', 'patching', 'testing'].includes(status)
}

function buildWorkspaceGroups(tasks: TaskSummary[]): WorkspaceGroup[] {
  const workspaceMap = new Map<string, TaskSummary[]>()
  for (const task of tasks) {
    const key = normalizePath(task.repository_path || 'Workspace')
    const list = workspaceMap.get(key) ?? []
    list.push(task)
    workspaceMap.set(key, list)
  }

  const basenameCount = new Map<string, number>()
  for (const key of workspaceMap.keys()) {
    const label = getWorkspaceLabel(key)
    basenameCount.set(label, (basenameCount.get(label) ?? 0) + 1)
  }

  return [...workspaceMap.entries()]
    .map(([key, groupTasks]) => {
      const label = getWorkspaceLabel(key)
      const detail = basenameCount.get(label) && basenameCount.get(label)! > 1 ? getWorkspaceDetail(key) : null
      return {
        key,
        label,
        detail,
        tasks: [...groupTasks].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
      }
    })
    .sort((a, b) => a.label.localeCompare(b.label) || a.key.localeCompare(b.key))
}

export function TaskList({ tasks, selectedTaskId, loading, onSelect, onDelete }: Props) {
  const groups = buildWorkspaceGroups(tasks)

  return (
    <div className="sidebar-card">
      <div className="panel-title-row sidebar-title-row">
        <div>
          <h2>Conversations</h2>
          <p className="muted">Grouped by workspace.</p>
        </div>
      </div>

      {loading ? <div className="empty-subtle">Loading conversations...</div> : null}

      <div className="workspace-groups">
        {groups.length === 0 && !loading ? (
          <div className="empty-subtle">No conversations yet. Start one from the button above.</div>
        ) : null}

        {groups.map((group) => (
          <section key={group.key} className="workspace-group">
            <div className="workspace-group-header">
              <div>
                <div className="workspace-group-title">{group.label}</div>
                {group.detail ? <div className="workspace-group-detail muted">{group.detail}</div> : null}
              </div>
              <span className="workspace-group-count">{group.tasks.length}</span>
            </div>

            <div className="task-list">
              {group.tasks.map((task) => (
                <article key={task.id} className={`task-list-item ${selectedTaskId === task.id ? 'selected' : ''}`}>
                  <button type="button" className="task-list-item-main" onClick={() => onSelect(task.id)}>
                    <div className="task-list-item-header">
                      <strong>{task.title}</strong>
                      <StatusBadge status={task.status} />
                    </div>
                    <p className="muted">{task.summary ?? task.description ?? 'No summary yet.'}</p>
                    <div className="task-list-item-meta">
                      <span>{task.current_step ?? 'idle'}</span>
                      <span>{new Date(task.updated_at).toLocaleTimeString()}</span>
                    </div>
                  </button>
                  <button
                    type="button"
                    className="task-delete-button"
                    onClick={() => onDelete(task.id)}
                    disabled={isActiveStatus(task.status)}
                  >
                    Delete
                  </button>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
