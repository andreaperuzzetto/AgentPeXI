const MOCK_TASKS = [
  { id: 'SCH-1', name: 'Health check SSD', interval: 'Ogni 5 min', enabled: true },
  { id: 'SCH-2', name: 'Sync stato agenti', interval: 'Ogni 30 sec', enabled: true },
  { id: 'SCH-3', name: 'Report giornaliero analytics', interval: 'Ogni giorno 08:00', enabled: false },
]

export function SchedulerPanel() {
  return (
    <section>
      <div style={{ padding: '10px 10px 6px' }}>
        <span className="section-label">Scheduler</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {MOCK_TASKS.map((t) => (
          <div
            key={t.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '5px 10px',
              fontSize: '0.75rem',
            }}
          >
            <span
              className={t.enabled ? 'status-dot status-dot--ok' : 'status-dot status-dot--off'}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: t.enabled ? 'var(--text-primary)' : 'var(--text-faint)' }}>
                {t.name}
              </div>
              <div className="font-data" style={{ fontSize: '0.625rem', color: 'var(--text-faint)' }}>
                {t.interval}
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
