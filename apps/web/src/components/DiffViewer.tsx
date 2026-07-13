export function DiffViewer({ diff }: { diff: string | null }) {
  return (
    <section className="card">
      <div className="panel-title-row">
        <div>
          <h3>Diff</h3>
          <p className="muted">What changed in the workspace.</p>
        </div>
      </div>
      {diff ? <pre className="diff-viewer">{diff}</pre> : <div className="empty-subtle">No patch yet.</div>}
    </section>
  )
}
