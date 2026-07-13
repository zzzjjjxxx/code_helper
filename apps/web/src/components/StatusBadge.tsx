import type { TaskStatus } from '../types'

const STATUS_LABELS: Record<TaskStatus, string> = {
  created: 'Created',
  queued: 'Queued',
  reading: 'Reading',
  analyzing: 'Analyzing',
  patching: 'Patching',
  testing: 'Testing',
  awaiting_review: 'Review',
  rolled_back: 'Rolled back',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export function StatusBadge({ status }: { status: TaskStatus }) {
  return <span className={`status-badge status-${status}`}>{STATUS_LABELS[status]}</span>
}
