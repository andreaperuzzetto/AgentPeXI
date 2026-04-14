export function AnalyticsPanel() {
  return (
    <section>
      <div style={{ padding: '10px 12px 6px' }}>
        <span className="section-label">Analytics</span>
      </div>
      <div
        style={{
          padding: '24px 12px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 12,
          minHeight: 140,
        }}
      >
        {/* Placeholder chart area */}
        <div
          style={{
            width: '100%',
            height: 80,
            background: 'var(--bg-surface-2)',
            borderRadius: 2,
            border: '1px dashed var(--border-subtle)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <svg width="120" height="40" viewBox="0 0 120 40" fill="none">
            <polyline
              points="0,35 15,28 30,30 45,18 60,22 75,10 90,14 105,5 120,8"
              stroke="var(--accent-dim)"
              strokeWidth="1.5"
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <span style={{ fontSize: '0.6875rem', color: 'var(--text-faint)' }}>
          Revenue / costi / margine — disponibile in Fase 3
        </span>
      </div>
    </section>
  )
}
