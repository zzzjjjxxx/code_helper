import { useEffect, useMemo, useState } from 'react'

import { api } from './api/client'
import { TaskDetailPanel } from './components/TaskDetail'
import { TaskList } from './components/TaskList'
import type { TaskSummary } from './types'

const WORKSPACE_STORAGE_KEY = 'code_helper.workspace_path'
const THEME_STORAGE_KEY = 'code_helper.theme'

function getWorkspaceLabel(path: string): string {
  const normalized = path.replace(/\\/g, '/').replace(/\/+$/, '')
  const segments = normalized.split('/').filter(Boolean)
  return segments.length > 0 ? segments[segments.length - 1] : 'Workspace'
}

export default function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [conversationTitle, setConversationTitle] = useState('')
  const [workspacePath, setWorkspacePath] = useState('')
  const [workspaceLabel, setWorkspaceLabel] = useState('')
  const [workspaceNote, setWorkspaceNote] = useState('')
  const [pickingWorkspace, setPickingWorkspace] = useState(false)
  const [creatingConversation, setCreatingConversation] = useState(false)
  const [theme, setTheme] = useState<'light' | 'dark'>('light')

  const activeTask = useMemo(() => tasks.find((task) => task.id === activeTaskId) ?? null, [tasks, activeTaskId])

  const refreshTasks = async () => {
    try {
      const data = await api.listTasks()
      setTasks(data)
      setError(null)
      setActiveTaskId((current) => (current && data.some((task) => task.id === current) ? current : null))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const savedWorkspace = window.localStorage.getItem(WORKSPACE_STORAGE_KEY) ?? ''
    setWorkspacePath(savedWorkspace)
    setWorkspaceLabel(savedWorkspace ? getWorkspaceLabel(savedWorkspace) : '')
    setWorkspaceNote(savedWorkspace ? '' : 'Pick a folder to label this conversation.')
    setTheme((window.localStorage.getItem(THEME_STORAGE_KEY) as 'light' | 'dark' | null) ?? 'light')
    void refreshTasks()
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem(THEME_STORAGE_KEY, theme)
  }, [theme])

  const openCreateDialog = () => {
    setConversationTitle(`Conversation ${new Date().toLocaleString()}`)
    const savedWorkspace = window.localStorage.getItem(WORKSPACE_STORAGE_KEY) ?? ''
    setWorkspacePath(savedWorkspace)
    setWorkspaceLabel(savedWorkspace ? getWorkspaceLabel(savedWorkspace) : '')
    setWorkspaceNote(savedWorkspace ? '' : 'Pick a folder to label this conversation.')
    setCreateDialogOpen(true)
  }

  const pickWorkspace = async () => {
    setPickingWorkspace(true)
    try {
      const response = await api.pickWorkspace()
      if (!response.path) {
        setWorkspaceNote("No workspace was selected.")
        return
      }
      setWorkspacePath(response.path)
      setWorkspaceLabel(getWorkspaceLabel(response.path))
      setWorkspaceNote("")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to choose a workspace folder")
    } finally {
      setPickingWorkspace(false)
    }
  }

  const handleCreateConversation = async (event: { preventDefault(): void }) => {
    event.preventDefault()
    setCreatingConversation(true)
    try {
      const title = conversationTitle.trim() || `Conversation ${new Date().toLocaleString()}`
      const repositoryPath = workspacePath.trim()
      if (!repositoryPath) {
        setWorkspaceNote("Enter the full workspace path before starting the conversation.")
        return
      }
      const created = await api.createTask({ title, repository_path: repositoryPath })
      window.localStorage.setItem(WORKSPACE_STORAGE_KEY, repositoryPath)
      setActiveTaskId(created.id)
      setCreateDialogOpen(false)
      await refreshTasks()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create a new conversation")
    } finally {
      setCreatingConversation(false)
    }
  }

  const handleDeleteConversation = async (taskId: string) => {
    const task = tasks.find((item) => item.id === taskId)
    if (!task) {
      return
    }
    if (!window.confirm(`Delete conversation "${task.title}"?`)) {
      return
    }
    try {
      await api.deleteTask(taskId)
      setActiveTaskId((current) => (current === taskId ? null : current))
      await refreshTasks()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete the conversation')
    }
  }

  return (
    <div className="app-shell chat-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">R&amp;D Assistant MVP</p>
          <h1>Conversation workspace</h1>
          <p className="muted">Workspaces are grouped on the left. Start a new conversation by choosing a workspace first.</p>
        </div>
        <div className="topbar-actions">
          <button className="secondary-button" type="button" onClick={() => setTheme((current) => (current === 'light' ? 'dark' : 'light'))}>
            {theme === 'light' ? 'Dark mode' : 'Light mode'}
          </button>
          <button className="primary-button" type="button" onClick={openCreateDialog}>
            New conversation
          </button>
          <button className="secondary-button" type="button" onClick={() => void refreshTasks()}>
            Refresh
          </button>
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      <main className="workspace workspace-chat">
        <aside className="sidebar panel">
          <TaskList
            tasks={tasks}
            selectedTaskId={activeTaskId}
            loading={loading}
            onSelect={setActiveTaskId}
            onDelete={(taskId) => void handleDeleteConversation(taskId)}
          />
        </aside>

        <section className="content panel">
          {activeTask ? (
            <TaskDetailPanel
              key={activeTask.id}
              taskId={activeTask.id}
              onRefreshTasks={() => void refreshTasks()}
              onSelectTask={setActiveTaskId}
            />
          ) : (
            <div className="empty-state chat-empty-state">
              <h2>Start a conversation</h2>
              <p>Open an existing thread on the left or create a new one with a workspace folder.</p>
            </div>
          )}
        </section>
      </main>

      {createDialogOpen ? (
        <div className="dialog-backdrop" onClick={() => setCreateDialogOpen(false)}>
          <form className="dialog-card" onSubmit={handleCreateConversation} onClick={(event) => event.stopPropagation()}>
            <div className="panel-title-row">
              <div>
                <h2>New conversation</h2>
                <p className="muted">Choose the workspace for this conversation.</p>
              </div>
              <button type="button" className="secondary-button" onClick={() => setCreateDialogOpen(false)}>
                Close
              </button>
            </div>
            <label className="dialog-field">
              <span>Conversation title</span>
              <input value={conversationTitle} onChange={(event) => setConversationTitle(event.target.value)} placeholder="Conversation title" />
            </label>
            <div className="dialog-field">
              <span>Workspace</span>
              <div className="workspace-picker">
                <button type="button" className="secondary-button" onClick={() => void pickWorkspace()} disabled={pickingWorkspace || creatingConversation}>
                  {pickingWorkspace ? "Opening..." : "Browse folder"}
                </button>
                <div className="workspace-picker-meta">
                  <strong>{workspaceLabel || 'No folder selected'}</strong>                  <input
                    value={workspacePath}
                    onChange={(event) => {
                      const value = event.target.value
                      setWorkspacePath(value)
                      setWorkspaceLabel(value ? getWorkspaceLabel(value) : '')
                      setWorkspaceNote('')
                    }}
                    placeholder='D:\\path\\to\\your\\workspace'
                    aria-label='Workspace path'
                  />
                  <span className='muted'>{workspaceNote || 'Enter the exact path used by the backend. The browser folder picker may not expose an absolute path.'}</span>
                </div>
              </div>
            </div>
            <div className="dialog-actions">
              <button type="button" className="secondary-button" onClick={() => setCreateDialogOpen(false)}>
                Cancel
              </button>
              <button className="primary-button" type="submit" disabled={creatingConversation || pickingWorkspace}>
                {creatingConversation ? "Creating..." : "Start conversation"}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  )
}