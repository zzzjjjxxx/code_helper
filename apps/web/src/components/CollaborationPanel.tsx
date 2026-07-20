import type { TaskEvent } from '../types'

function getRole(type: string): string {
  // DOC_ANCHOR: collaboration.role_map
  if (type.startsWith('agent.planner.')) {
    return 'Planner'
  }
  if (type.startsWith('agent.executor.')) {
    return 'Executor'
  }
  if (type.startsWith('agent.reviewer.')) {
    return 'Reviewer'
  }
  if (type === 'branch.created' || type === 'branch.selected' || type === 'replan.requested' || type === 'branch.comparison.completed') {
    return 'Coordinator'
  }
  if (type === 'branch.reverted') {
    return 'Recovery'
  }
  return 'System'
}

function getBranch(payload: Record<string, unknown>): string | null {
  const value = payload.branch
  return typeof value === 'string' && value ? value : null
}

function getAction(payload: Record<string, unknown>): string | null {
  const value = payload.action
  return typeof value === 'string' && value ? value : null
}

export function CollaborationPanel({ events }: { events: TaskEvent[] }) {
  // DOC_ANCHOR: collaboration.panel
  const collaborationEvents = events.filter((event) => {
    return (
      event.type.startsWith('agent.') ||
      event.type === 'branch.created' ||
      event.type === 'branch.selected' ||
      event.type === 'replan.requested' ||
      event.type === 'branch.reverted' ||
      event.type === 'branch.comparison.completed'
    )
  })

  return (
    <section className="card">
      <div className="panel-title-row">
        <div>
          <h3>Collaboration</h3>
          <p className="muted">Planner, executor, reviewer, and coordinator hand off work to each other.</p>
        </div>
      </div>
      {collaborationEvents.length === 0 ? (
        <div className="empty-subtle">No agent collaboration events yet.</div>
      ) : (
        <div className="artifact-list">
          {collaborationEvents.map((event) => {
            const payload = event.payload as Record<string, unknown>
            return (
              <article key={`${event.sequence}-${event.event_id}`} className="timeline-item">
                <div className="timeline-item-top">
                  <strong>{getRole(event.type)}</strong>
                  <span className="muted">#{event.sequence}</span>
                </div>
                <p>{event.message}</p>
                <div className="timeline-meta">
                  {getBranch(payload) ? <small className="muted">Branch: {getBranch(payload)}</small> : null}
                  {getAction(payload) ? <small className="muted">Action: {getAction(payload)}</small> : null}
                  {typeof payload.winning_branch === 'string' ? <small className="muted">Winner: {payload.winning_branch}</small> : null}
                </div>
                {typeof payload.summary === 'string' && payload.summary ? (
                  <small className="muted">Summary: {payload.summary}</small>
                ) : null}
                {typeof payload.rationale === 'string' && payload.rationale ? (
                  <small className="muted">Rationale: {payload.rationale}</small>
                ) : null}
                {Array.isArray(payload.highlights) && payload.highlights.length > 0 ? (
                  <ul style={{ margin: '0.5rem 0 0', paddingLeft: '1.1rem' }}>
                    {payload.highlights.filter((item): item is string => typeof item === 'string').map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}


