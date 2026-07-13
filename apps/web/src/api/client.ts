import { ArtifactRecord, SecurityPolicy, TaskCreateRequest, TaskDetail, TaskEvent, TaskStatus, TaskSummary, TestOutcome } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })

  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || response.statusText)
  }

  return response.json() as Promise<T>
}

export const api = {
  baseUrl: API_BASE,
  listTasks: () => request<TaskSummary[]>('/tasks'),
  getTask: (taskId: string) => request<TaskDetail>(`/tasks/${taskId}`),
  createTask: (payload: TaskCreateRequest) => request<TaskDetail>('/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  runTask: (taskId: string) => request<{ task: TaskSummary; accepted: boolean }>(`/tasks/${taskId}/run`, { method: 'POST' }),
  rollbackTask: (taskId: string) => request<{ task: TaskSummary; restored_snapshot_id: string | null; message: string }>(`/tasks/${taskId}/rollback`, { method: 'POST' }),
  listTaskArtifacts: (taskId: string) => request<ArtifactRecord[]>(`/tasks/${taskId}/artifacts`),
  getSecurityPolicy: () => request<SecurityPolicy>('/security/policy'),
  eventStreamUrl: (taskId: string, after = 0) => `${API_BASE}/tasks/${taskId}/events?after=${after}`,
}

export type {
  ArtifactRecord,
  CommandRule,
  MemoryRecord,
  RetrievalHit,
  SecurityPolicy,
  TaskCreateRequest,
  TaskDetail,
  TaskEvent,
  TaskStatus,
  TaskSummary,
  TestOutcome,
}
