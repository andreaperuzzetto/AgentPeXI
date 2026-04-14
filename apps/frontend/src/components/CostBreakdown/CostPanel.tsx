export function CostPanel() {
  return (
    <section>
      <div style={{ padding: '10px 10px 6px' }}>
        <span className="section-label">Cost Breakdown</span>
      </div>
      <div
        style={{
          padding: '16px 10px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 10,
          minHeight: 100,
        }}
      >
        {/* Placeholder pie chart icon */}
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
          <circle cx="24" cy="24" r="20" stroke="var(--border-subtle)" strokeWidth="2" />
          <path
            d="M24 4 A20 20 0 0 1 44 24 L24 24 Z"
            fill="var(--accent-dim)"
          />
          <path
            d="M44 24 A20 20 0 0 1 24 44 L24 24 Z"
            fill="var(--border-strong)"
          />
        </svg>
        <span style={{ fontSize: '0.6875rem', color: 'var(--text-faint)', textAlign: 'center' }}>
          Costi per agente / tool / giorno — disponibile in Fase 3
        </span>
      </div>
    </section>
  )
}
