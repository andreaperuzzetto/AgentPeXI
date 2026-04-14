const PLACEHOLDER_JSON = {
  task_id: 'tsk_abc123',
  agent: 'research',
  steps: [
    { step: 1, type: 'llm_call', description: 'Analisi keyword nicchia', duration_ms: 1240 },
    { step: 2, type: 'tool_call', description: 'Tavily search: "etsy wedding planner"', duration_ms: 680 },
    { step: 3, type: 'llm_call', description: 'Sintesi risultati e ranking', duration_ms: 920 },
  ],
}

interface Props {
  listingId: string
  onClose: () => void
}

export function TaskDrawer({ listingId, onClose }: Props) {
  return (
    <div
      style={{
        borderTop: '1px solid var(--border-strong)',
        background: 'var(--bg-surface-2)',
        padding: '10px 12px',
        maxHeight: 260,
        overflowY: 'auto',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span className="section-label">Task Detail — {listingId}</span>
        <button
          onClick={onClose}
          aria-label="Chiudi dettaglio"
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-muted)',
            cursor: 'pointer',
            fontSize: '0.8125rem',
            fontFamily: 'var(--font-body)',
            padding: '2px 6px',
          }}
        >
          ✕
        </button>
      </div>

      {/* Timeline placeholder */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {PLACEHOLDER_JSON.steps.map((step) => (
          <div
            key={step.step}
            style={{
              display: 'grid',
              gridTemplateColumns: '20px 1fr auto',
              alignItems: 'start',
              gap: 8,
              padding: '4px 0',
              fontSize: '0.75rem',
            }}
          >
            <span
              className="font-data"
              style={{ color: 'var(--text-faint)', fontSize: '0.625rem', paddingTop: 2 }}
            >
              #{step.step}
            </span>
            <div>
              <div style={{ color: 'var(--text-primary)' }}>{step.description}</div>
              <span
                style={{
                  display: 'inline-block',
                  marginTop: 2,
                  fontSize: '0.625rem',
                  color: 'var(--text-faint)',
                  textTransform: 'uppercase' as const,
                  letterSpacing: '0.04em',
                }}
              >
                {step.type}
              </span>
            </div>
            <span className="font-data" style={{ color: 'var(--text-muted)', fontSize: '0.6875rem' }}>
              {step.duration_ms}ms
            </span>
          </div>
        ))}
      </div>

      {/* Raw JSON placeholder */}
      <details style={{ marginTop: 10 }}>
        <summary
          style={{
            fontSize: '0.6875rem',
            color: 'var(--text-faint)',
            cursor: 'pointer',
            userSelect: 'none',
          }}
        >
          JSON completo (placeholder)
        </summary>
        <pre
          className="font-data"
          style={{
            marginTop: 6,
            padding: 8,
            background: 'var(--bg-base)',
            borderRadius: 2,
            fontSize: '0.625rem',
            lineHeight: 1.5,
            color: 'var(--text-muted)',
            overflow: 'auto',
            maxHeight: 120,
          }}
        >
          {JSON.stringify(PLACEHOLDER_JSON, null, 2)}
        </pre>
      </details>
    </div>
  )
}
