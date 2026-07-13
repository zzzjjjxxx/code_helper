import { useMemo } from 'react'

import type { TaskEvent } from '../types'

interface BranchView {
  branch: string
  turn: number
  agent: string
  profile: string
  plannerAction: string
  plannerSummary: string
  plannerRationale: string
  filesToRead: string[]
  changedFiles: string[]
  testPassed: boolean | null
  reviewAction: string
  reviewSummary: string
  reviewRationale: string
  score: number
  selected: boolean
  status: 'planned' | 'selected' | 'patched' | 'approved' | 'revised' | 'rejected' | 'reverted'
  createdAt: string
  updatedAt: string
}

interface TurnGroup {
  turn: number
  selectedBranchId: string | null
  branches: BranchView[]
  candidateCount: number
  updatedAt: string
}

function emptyBranch(branch: string): BranchView {
  return {
    branch,
    turn: 0,
    agent: '',
    profile: '',
    plannerAction: 'patch',
    plannerSummary: '',
    plannerRationale: '',
    filesToRead: [],
    changedFiles: [],
    testPassed: null,
    reviewAction: '',
    reviewSummary: '',
    reviewRationale: '',
    score: 0,
    selected: false,
    status: 'planned',
    createdAt: '',
    updatedAt: '',
  }
}

function unique(values: string[]): string[] {
  return values.filter((value, index, array) => value && array.indexOf(value) === index)
}

function normalizeList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.trim()).map((item) => item.trim()) : []
}

function normalizeTurn(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function ensureTurn(turns: Map<number, TurnGroup>, turn: number): TurnGroup {
  const existing = turns.get(turn)
  if (existing) {
    return existing
  }

  const created: TurnGroup = {
    turn,
    selectedBranchId: null,
    branches: [],
    candidateCount: 0,
    updatedAt: '',
  }
  turns.set(turn, created)
  return created
}

function ensureBranch(group: TurnGroup, branchName: string): BranchView {
  const existing = group.branches.find((branch) => branch.branch === branchName)
  if (existing) {
    return existing
  }

  const branch = emptyBranch(branchName)
  branch.turn = group.turn
  group.branches.push(branch)
  return branch
}
function findBranchGroup(turns: Map<number, TurnGroup>, branchName: string): TurnGroup | null {
  for (const group of turns.values()) {
    if (group.branches.some((branch) => branch.branch === branchName)) {
      return group
    }
  }
  return null
}

function applyCandidate(group: TurnGroup, candidate: Record<string, unknown>): void {
  const branchName = typeof candidate.branch === 'string' && candidate.branch ? candidate.branch : null
  if (!branchName) {
    return
  }

  const branch = ensureBranch(group, branchName)
  branch.turn = group.turn
  branch.agent = typeof candidate.agent === 'string' ? candidate.agent : branch.agent
  branch.profile = typeof candidate.profile === 'string' ? candidate.profile : branch.profile
  branch.plannerAction = typeof candidate.action === 'string' ? candidate.action : branch.plannerAction
  branch.plannerSummary = typeof candidate.summary === 'string' ? candidate.summary : branch.plannerSummary
  branch.plannerRationale = typeof candidate.rationale === 'string' ? candidate.rationale : branch.plannerRationale
  branch.filesToRead = unique([...branch.filesToRead, ...normalizeList(candidate.files_to_read)])
  branch.score = typeof candidate.score === 'number' ? candidate.score : branch.score
  branch.selected = candidate.selected === true
  if (branch.selected) {
    branch.status = 'selected'
    group.selectedBranchId = branch.branch
  }
}

function buildTurns(events: TaskEvent[]): TurnGroup[] {
  const turns = new Map<number, TurnGroup>()

  for (const event of events) {
    const payload = event.payload as Record<string, unknown>
    const branchName = typeof payload.branch === 'string' ? payload.branch : null
    const turnNumber = normalizeTurn(payload.turn)

    if (event.type === 'branch.selected') {
      const group = ensureTurn(turns, turnNumber)
      group.updatedAt = event.created_at
      const candidates = Array.isArray(payload.candidates) ? payload.candidates : []
      for (const candidate of candidates) {
        if (candidate && typeof candidate === 'object') {
          applyCandidate(group, candidate as Record<string, unknown>)
        }
      }
      if (branchName) {
        const branch = ensureBranch(group, branchName)
        branch.selected = true
        branch.status = 'selected'
        group.selectedBranchId = branch.branch
      }
      continue
    }

    if (!branchName) {
      continue
    }

    const group = turnNumber > 0 ? ensureTurn(turns, turnNumber) : findBranchGroup(turns, branchName) ?? ensureTurn(turns, 0)
    group.updatedAt = event.created_at
    const branch = ensureBranch(group, branchName)
    branch.updatedAt = event.created_at

    if (event.type === 'branch.created') {
      branch.turn = turnNumber || branch.turn
      branch.agent = typeof payload.agent === 'string' ? payload.agent : branch.agent
      branch.profile = typeof payload.profile === 'string' ? payload.profile : branch.profile
      branch.plannerAction = typeof payload.action === 'string' ? payload.action : branch.plannerAction
      branch.plannerSummary = typeof payload.summary === 'string' ? payload.summary : branch.plannerSummary
      branch.plannerRationale = typeof payload.rationale === 'string' ? payload.rationale : branch.plannerRationale
      branch.filesToRead = unique([...branch.filesToRead, ...normalizeList(payload.files_to_read)])
      branch.score = typeof payload.score === 'number' ? payload.score : branch.score
      branch.createdAt = event.created_at
      if (payload.selected === true) {
        branch.selected = true
        branch.status = 'selected'
        group.selectedBranchId = branch.branch
      } else if (branch.status !== 'selected') {
        branch.status = 'planned'
      }
      continue
    }

    if (event.type === 'agent.planner.completed') {
      branch.plannerSummary = typeof payload.summary === 'string' ? payload.summary : branch.plannerSummary
      branch.plannerRationale = typeof payload.rationale === 'string' ? payload.rationale : branch.plannerRationale
      branch.plannerAction = typeof payload.action === 'string' ? payload.action : branch.plannerAction
      branch.agent = typeof payload.agent === 'string' ? payload.agent : branch.agent
      branch.profile = typeof payload.agent_profile === 'string' ? payload.agent_profile : branch.profile
      if (payload.selected_branch === branch.branch) {
        branch.selected = true
        branch.status = 'selected'
        group.selectedBranchId = branch.branch
      }
    }

    if (event.type === 'agent.executor.completed') {
      branch.changedFiles = unique([...branch.changedFiles, ...normalizeList(payload.changed_files)])
      branch.testPassed = typeof payload.passed === 'boolean' ? payload.passed : branch.testPassed
      branch.status = branch.testPassed ? 'patched' : branch.status
    }

    if (event.type === 'agent.reviewer.completed') {
      branch.reviewAction = typeof payload.action === 'string' ? payload.action : branch.reviewAction
      branch.reviewSummary = typeof payload.summary === 'string' ? payload.summary : branch.reviewSummary
      branch.reviewRationale = typeof payload.rationale === 'string' ? payload.rationale : branch.reviewRationale
      if (branch.reviewAction === 'approve') {
        branch.status = 'approved'
      } else if (branch.reviewAction === 'reject') {
        branch.status = 'rejected'
      } else if (branch.reviewAction === 'revise') {
        branch.status = 'revised'
      }
    }

    if (event.type === 'replan.requested') {
      branch.status = 'revised'
    }

    if (event.type === 'branch.reverted') {
      branch.status = 'reverted'
    }

    if (payload.selected === true) {
      branch.selected = true
      branch.status = 'selected'
      group.selectedBranchId = branch.branch
    }
  }

  return Array.from(turns.values())
    .filter((group) => group.turn > 0 || group.branches.length > 0)
    .sort((left, right) => left.turn - right.turn)
    .map((group) => ({
      ...group,
      branches: [...group.branches].sort((left, right) => {
        if (left.selected !== right.selected) {
          return left.selected ? -1 : 1
        }
        if (right.score !== left.score) {
          return right.score - left.score
        }
        return left.branch.localeCompare(right.branch)
      }),
      candidateCount: group.branches.length,
    }))
}

function describeDelta(reference: BranchView | null, current: BranchView): string[] {
  if (!reference) {
    return ['No selected branch to compare against yet']
  }

  const deltas: string[] = []
  if (reference.plannerAction !== current.plannerAction) {
    deltas.push(`Planner action: ${reference.plannerAction} -> ${current.plannerAction}`)
  }

  const referenceFiles = reference.filesToRead.join(', ')
  const currentFiles = current.filesToRead.join(', ')
  if (referenceFiles !== currentFiles) {
    deltas.push(`Context files: ${currentFiles || 'none'}`)
  }

  if (reference.reviewAction !== current.reviewAction && current.reviewAction) {
    deltas.push(`Review: ${reference.reviewAction || 'pending'} -> ${current.reviewAction}`)
  }

  if (reference.testPassed !== current.testPassed && current.testPassed !== null) {
    deltas.push(`Test result: ${reference.testPassed === null ? 'pending' : reference.testPassed ? 'pass' : 'fail'} -> ${current.testPassed ? 'pass' : 'fail'}`)
  }

  return deltas
}

function statusLabel(status: BranchView['status']): string {
  switch (status) {
    case 'selected':
      return 'Selected'
    case 'approved':
      return 'Approved'
    case 'rejected':
      return 'Rejected'
    case 'revised':
      return 'Needs replan'
    case 'reverted':
      return 'Reverted'
    case 'patched':
      return 'Patched'
    default:
      return 'Planned'
  }
}

export function BranchComparisonPanel({ events }: { events: TaskEvent[] }) {
  const turns = useMemo(() => buildTurns(events), [events])

  if (turns.length === 0) {
    return (
      <section className="card">
        <div className="panel-title-row">
          <div>
            <h3>Plan branches</h3>
            <p className="muted">No branch history yet.</p>
          </div>
        </div>
        <div className="empty-subtle">Run a task to generate planner branches.</div>
      </section>
    )
  }

  const totalCandidates = turns.reduce((sum, group) => sum + group.candidateCount, 0)

  return (
    <section className="card">
      <div className="panel-title-row">
        <div>
          <h3>Plan branches</h3>
          <p className="muted">Parallel branch comparison by turn. The selected branch is highlighted first.</p>
        </div>
        <span className="muted">
          {turns.length} turns / {totalCandidates} candidates
        </span>
      </div>

      <div className="artifact-list">
        {turns.map((group) => {
          const selected = group.branches.find((branch) => branch.selected) ?? null
          return (
            <article key={group.turn} className="timeline-item">
              <div className="timeline-item-top">
                <strong>Turn {group.turn}</strong>
                <span className="muted">{group.candidateCount} branches</span>
              </div>
              <p className="muted">
                {selected ? `Selected ${selected.branch} (${selected.agent || 'planner'})` : 'No branch selected yet.'}
              </p>

              <div className="branch-grid">
                {group.branches.map((branch) => {
                  const reference = selected && branch.branch !== selected.branch ? selected : null
                  const deltas = branch.branch !== (selected?.branch ?? '') ? describeDelta(reference, branch) : []
                  return (
                    <article
                      key={branch.branch}
                      className="branch-card"
                      style={{
                        border: branch.selected ? '1px solid rgba(76, 175, 80, 0.85)' : '1px solid rgba(255, 255, 255, 0.08)',
                        boxShadow: branch.selected ? '0 0 0 1px rgba(76, 175, 80, 0.18), 0 12px 28px rgba(0, 0, 0, 0.2)' : undefined,
                        background: branch.selected ? 'rgba(76, 175, 80, 0.08)' : undefined,
                      }}
                    >
                      <div className="branch-card-header">
                        <div>
                          <div className="branch-kicker">
                            {branch.agent || 'planner'} / {branch.profile || 'default'}
                          </div>
                          <h4>{branch.branch}</h4>
                        </div>
                        <div style={{ display: 'grid', gap: '0.35rem', justifyItems: 'end' }}>
                          <span className={`branch-pill branch-${branch.status}`}>{statusLabel(branch.status)}</span>
                          <span className="muted">score {branch.score.toFixed(2)}</span>
                        </div>
                      </div>

                      <div className="branch-sections">
                        <div className="branch-section">
                          <span className="muted">Planner</span>
                          <strong>{branch.plannerAction}</strong>
                          <p>{branch.plannerSummary || 'No planner summary yet.'}</p>
                          {branch.plannerRationale ? <small className="muted">{branch.plannerRationale}</small> : null}
                        </div>

                        <div className="branch-section">
                          <span className="muted">Executor</span>
                          <strong>{branch.testPassed === null ? 'Pending' : branch.testPassed ? 'Test passed' : 'Test failed'}</strong>
                          <p>{branch.changedFiles.length > 0 ? branch.changedFiles.join(', ') : 'No file changes captured yet.'}</p>
                        </div>

                        <div className="branch-section">
                          <span className="muted">Reviewer</span>
                          <strong>{branch.reviewAction || 'Pending'}</strong>
                          <p>{branch.reviewSummary || 'No review yet.'}</p>
                          {branch.reviewRationale ? <small className="muted">{branch.reviewRationale}</small> : null}
                        </div>
                      </div>

                      <div className="branch-meta">
                        <div>
                          <span className="muted">Context</span>
                          <div>{branch.filesToRead.length > 0 ? branch.filesToRead.join(', ') : 'No extra files requested'}</div>
                        </div>
                        <div>
                          <span className="muted">Compare</span>
                          <div>{branch.selected ? 'Winner for this turn' : deltas.join(' · ') || 'Alternative branch'}</div>
                        </div>
                      </div>
                    </article>
                  )
                })}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}