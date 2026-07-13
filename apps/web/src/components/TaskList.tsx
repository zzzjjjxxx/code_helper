import { StatusBadge } from './StatusBadge'
import type { TaskSummary } from '../types'

interface Props {
  tasks: TaskSummary[]
  selectedTaskId: string | null
  loading: boolean
  onSelect: (taskId: string) => void
}

export function TaskList({ tasks, selectedTaskId, loading, onSelect }: Props) {
  return (
    <div>
      <div className="panel-title-row">
        <div>
          <h2>Tasks</h2>
          <p className="muted">Live state, events, and rollback-ready snapshots.</p>
        </div>
      </div>

      {loading ? <div className="empty-subtle">Loading tasks…</div> : null}

      <div className="task-list">
        {tasks.length === 0 && !loading ? (
          <div className="empty-subtle">No tasks yet. Create the seeded demo task to get started.</div>
        ) : null}

        {tasks.map((task) => (
          <button
            key={task.id}
            type="button"
            className={`task-list-item ${selectedTaskId === task.id ? 'selected' : ''}`}
            onClick={() => onSelect(task.id)}
          >
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
        ))}
      </div>
    </div>
  )
}
