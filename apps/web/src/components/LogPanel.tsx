import type { TestOutcome } from '../types'

export function LogPanel({ result, lastError }: { result: TestOutcome | null; lastError: string | null }) {
  return (
    <section className="card">
      <div className="panel-title-row">
        <div>
          <h3>Test logs</h3>
          <p className="muted">Captured stdout, stderr, and exit code from the verification run.</p>
        </div>
      </div>

      {result ? (
        <div className="log-panel">
          <div className="log-grid">
            <div><span className="muted">Command</span><br />{result.command}</div>
            <div><span className="muted">Status</span><br />{result.passed ? 'Passed' : 'Failed'}</div>
            <div><span className="muted">Exit code</span><br />{result.return_code}</div>
            <div><span className="muted">Duration</span><br />{result.duration_ms} ms</div>
          </div>
          <div>
            <h4>stdout</h4>
            <pre>{result.stdout || 'No stdout'}</pre>
          </div>
          <div>
            <h4>stderr</h4>
            <pre>{result.stderr || 'No stderr'}</pre>
          </div>
        </div>
      ) : (
        <div className="empty-subtle">Run the task to see test output here.</div>
      )}

      {lastError ? <div className="error-inline">{lastError}</div> : null}
    </section>
  )
}
