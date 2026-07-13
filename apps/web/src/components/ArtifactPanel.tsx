import { useEffect, useMemo, useState } from 'react'

import type { ArtifactRecord } from '../types'

function formatArtifactContent(artifact: ArtifactRecord): string {
  if (artifact.type === 'test_report') {
    try {
      return `${JSON.stringify(JSON.parse(artifact.content), null, 2)}\n`
    } catch {
      return artifact.content
    }
  }

  return artifact.content
}

export function ArtifactPanel({ artifacts }: { artifacts: ArtifactRecord[] }) {
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null)

  useEffect(() => {
    if (artifacts.length === 0) {
      setSelectedArtifactId(null)
      return
    }

    if (!selectedArtifactId || !artifacts.some((artifact) => artifact.artifact_id === selectedArtifactId)) {
      setSelectedArtifactId(artifacts[0].artifact_id)
    }
  }, [artifacts, selectedArtifactId])

  const selectedArtifact = useMemo(
    () => artifacts.find((artifact) => artifact.artifact_id === selectedArtifactId) ?? artifacts[0] ?? null,
    [artifacts, selectedArtifactId],
  )

  return (
    <section className="card">
      <div className="panel-title-row">
        <div>
          <h3>Artifacts</h3>
          <p className="muted">Diffs, test reports, and other captured outputs.</p>
        </div>
        <span className="muted">{artifacts.length} items</span>
      </div>

      <div className="artifact-panel">
        <div className="artifact-list">
          {artifacts.length === 0 ? <div className="empty-subtle">No artifacts yet.</div> : null}
          {artifacts.map((artifact) => (
            <button
              key={artifact.artifact_id}
              type="button"
              className={`artifact-item ${selectedArtifact?.artifact_id === artifact.artifact_id ? 'selected' : ''}`}
              onClick={() => setSelectedArtifactId(artifact.artifact_id)}
            >
              <div className="artifact-item-top">
                <strong>{artifact.name}</strong>
                <span className="artifact-type">{artifact.type}</span>
              </div>
              <p className="muted">{new Date(artifact.created_at).toLocaleString()}</p>
            </button>
          ))}
        </div>

        <div className="artifact-preview">
          {selectedArtifact ? (
            <>
              <div className="artifact-preview-head">
                <div>
                  <strong>{selectedArtifact.name}</strong>
                  <div className="muted">{selectedArtifact.type}</div>
                </div>
                <div className="muted">{new Date(selectedArtifact.created_at).toLocaleString()}</div>
              </div>
              <pre className="artifact-content">{formatArtifactContent(selectedArtifact)}</pre>
            </>
          ) : (
            <div className="empty-subtle">Select an artifact to inspect it.</div>
          )}
        </div>
      </div>
    </section>
  )
}
