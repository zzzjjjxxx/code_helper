export type TaskStatus =
  | 'created'
  | 'queued'
  | 'reading'
  | 'analyzing'
  | 'patching'
  | 'testing'
  | 'awaiting_review'
  | 'rolled_back'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export type TaskStep = 'read' | 'analyze' | 'patch' | 'test' | 'review' | 'summarize' | 'rollback'

export interface TaskCreateRequest {
  title: string
  description?: string
  repository_path?: string | null
  focus_paths?: string[]
}

export interface CommandRule {
  executables: string[]
  args_prefix: string[]
}

export interface SecurityPolicy {
  workspace_root: string
  snapshot_root: string
  allowed_commands: CommandRule[]
  snapshot_integrity_required: boolean
}

export interface RetrievalHit {
  path: string
  score: number
  reason: string
  preview: string
}

export interface MemoryRecord {
  id: number | null
  memory_id: string
  task_id: string
  kind: string
  title: string
  content: string
  keywords: string[]
  related_files: string[]
  created_at: string
}

export interface TaskEvent {
  id: number | null
  event_id: string
  task_id: string
  sequence: number
  type: string
  step: TaskStep | string | null
  message: string
  payload: Record<string, unknown>
  created_at: string
}

export interface TestOutcome {
  command: string
  return_code: number
  stdout: string
  stderr: string
  passed: boolean
  duration_ms: number
}

export interface SnapshotRecord {
  id: string
  task_id: string
  label: string
  path: string
  created_at: string
}

export interface ArtifactRecord {
  id: number | null
  artifact_id: string
  task_id: string
  type: string
  name: string
  content: string
  created_at: string
}

export interface TaskSummary {
  id: string
  title: string
  description: string
  repository_path: string
  status: TaskStatus
  current_step: string | null
  created_at: string
  updated_at: string
  summary: string | null
  last_error: string | null
}

export interface TaskDetail extends TaskSummary {
  focus_paths: string[]
  latest_diff: string | null
  latest_test_result: TestOutcome | null
  latest_retrieval: RetrievalHit[]
  memory: MemoryRecord[]
  snapshots: SnapshotRecord[]
  events: TaskEvent[]
}
