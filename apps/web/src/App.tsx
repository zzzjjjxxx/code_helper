import { useEffect, useMemo, useState } from 'react'

import { api } from './api/client'
import { TaskDetailPanel } from './components/TaskDetail'
import { TaskList } from './components/TaskList'
import type { TaskSummary } from './types'

const DEMO_TASK_TITLE = 'Fix the demo workspace bug'

export default function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) ?? null,
    [tasks, selectedTaskId],
  )

  const refreshTasks = async () => {
    try {
      const data = await api.listTasks()
      setTasks(data)
      setError(null)
      setSelectedTaskId((current) => current ?? data[0]?.id ?? null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refreshTasks()
  }, [])

  const handleCreateDemoTask = async () => {
    try {
      const created = await api.createTask({ title: DEMO_TASK_TITLE })
      setSelectedTaskId(created.id)
      await refreshTasks()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create the demo task')
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">R&amp;D Assistant MVP</p>
          <h1>Debugging console</h1>
          <p className="muted">Read code, patch bugs, run tests, stream logs, and roll back safely.</p>
        </div>
        <div className="topbar-actions">
          <button className="primary-button" type="button" onClick={() => void handleCreateDemoTask()}>
            Create demo task
          </button>
          <button className="secondary-button" type="button" onClick={() => void refreshTasks()}>
            Refresh
          </button>
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      <main className="workspace">
        <aside className="sidebar panel">
          <TaskList tasks={tasks} selectedTaskId={selectedTaskId} onSelect={setSelectedTaskId} loading={loading} />
        </aside>

        <section className="content panel">
          {selectedTask ? (
            <TaskDetailPanel
              key={selectedTask.id}
              taskId={selectedTask.id}
              onRefreshTasks={() => void refreshTasks()}
              onSelectTask={setSelectedTaskId}
            />
          ) : (
            <div className="empty-state">
              <h2>No task selected</h2>
              <p>Create the seeded demo task to watch the agent read code, patch it, run tests, and support rollback.</p>
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
