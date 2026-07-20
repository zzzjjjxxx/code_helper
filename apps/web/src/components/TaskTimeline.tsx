import type { TaskEvent } from '../types'

function summarizePayload(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload)
  if (entries.length === 0) {
    return 'No payload'
  }

  return entries
    .slice(0, 3)
    .map(([key, value]) => {
      if (typeof value === 'string') {
        const clipped = value.length > 90 ? `${value.slice(0, 87)}...` : value
        return `${key}: ${clipped}`
      }

      if (Array.isArray(value)) {
        return `${key}: [${value.length} items]`
      }

      if (typeof value === 'object' && value !== null) {
        return `${key}: {...}`
      }

      return `${key}: ${String(value)}`
    })
    .join(' | ')
}

export function TaskTimeline({ events }: { events: TaskEvent[] }) {
  return (
    <section className="card">
      <div className="panel-title-row">
        <div>
          <h3>Task flow</h3>
          <p className="muted">A replayable event stream of the agent loop.</p>
        </div>
      </div>
      <div className="timeline">
        {events.length === 0 ? <div className="empty-subtle">Waiting for events...</div> : null}
        {events.map((event) => (
          <article key={`${event.sequence}-${event.event_id}`} className="timeline-item">
            <div className="timeline-item-top">
              <span className="timeline-type">
                #{event.sequence} {event.type}
              </span>
              <span className="timeline-time">{new Date(event.created_at).toLocaleTimeString()}</span>
            </div>
            <p>{event.message}</p>
            <div className="timeline-meta">
              {event.step ? <small className="muted">Step: {event.step}</small> : null}
              <small className="muted">{summarizePayload(event.payload)}</small>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}

