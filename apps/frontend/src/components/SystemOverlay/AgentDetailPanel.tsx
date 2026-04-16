import { useStore } from '../../store'

const EMPTY_STEPS: never[] = []

const STEP_TAG: Record<string, { label: string; color: string; bg: string }> = {
  llm_call:  { label: 'LLM',   color: 'var(--warn)',   bg: 'rgba(240,180,41,.08)' },
  tool_call: { label: 'TOOL',  color: 'var(--accent)', bg: 'rgba(45,232,106,.08)' },
  thinking:  { label: 'THINK', color: 'var(--ok)',     bg: 'rgba(45,232,106,.06)' },
}

export function AgentDetailPanel() {
  const selectedAgent    = useStore((s) => s.selectedAgent)
  const setSelectedAgent = useStore((s) => s.setSelectedAgent)
  const agent = useStore((s) => s.agents[selectedAgent ?? ''])
  const steps = useStore((s) => s.agentSteps[selectedAgent ?? ''] ?? EMPTY_STEPS)

  const isVisible = !!selectedAgent

  return (
    /* .page-detail — slides in from right */
    <div
      style={{
        position: 'absolute' as const,
        inset: 0,
        display: 'flex',
        transform: isVisible ? 'translateX(0)' : 'translateX(100%)',
        opacity: isVisible ? 1 : 0,
        pointerEvents: isVisible ? 'auto' : 'none',
        transition: 'transform .38s var(--e-out), opacity .28s var(--e-io)',
        willChange: 'transform, opacity',
      }}
    >
      {/* .ad-left — steps log */}
      <div
        style={{
          flex: 1,
          minWidth: 0,
          overflowY: 'auto',
          padding: '22px 20px 22px 24px',
          display: 'flex',
          flexDirection: 'column' as const,
          gap: 10,
          borderRight: '1px solid var(--b0)',
          background: 'var(--s1)',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 2 }}>
          <span className={agent?.status === 'running' ? 'status-dot status-dot--running' : 'status-dot status-dot--off'} />
          <span
            style={{
              fontFamily: 'var(--fh)',
              fontSize: 18,
              fontWeight: 700,
              letterSpacing: '0.04em',
              color: 'var(--tp)',
            }}
          >
            {selectedAgent ? selectedAgent.charAt(0).toUpperCase() + selectedAgent.slice(1) : ''} Agent
          </span>
          <button
            onClick={() => setSelectedAgent(null)}
            style={{
              marginLeft: 'auto',
              background: 'none',
              border: 'none',
              color: 'var(--tf)',
              cursor: 'pointer',
              fontSize: 18,
              padding: '4px 8px',
              transition: 'color .15s',
            }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = 'var(--tm)' }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = 'var(--tf)' }}
          >
            ✕
          </button>
        </div>

        {/* Current task */}
        {agent?.lastTask && (
          <div
            style={{
              fontFamily: 'var(--fd)',
              fontSize: 13,
              color: 'var(--tm)',
              padding: '8px 10px',
              borderRadius: 6,
              background: 'rgba(45,232,106,.04)',
              border: '1px solid rgba(45,232,106,.1)',
            }}
          >
            → {agent.lastTask}
          </div>
        )}

        {/* Section title */}
        <div
          style={{
            fontFamily: 'var(--fh)',
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase' as const,
            color: 'var(--tm)',
            marginTop: 4,
          }}
        >
          Reasoning live — {steps.length} step
        </div>

        {steps.length === 0 && (
          <p style={{ fontFamily: 'var(--fd)', fontSize: 14, color: 'var(--tf)', textAlign: 'center', marginTop: 24 }}>
            Nessuna attività registrata.<br />
            Gli step appariranno in tempo reale.
          </p>
        )}

        {/* Step list — .ad-step */}
        {[...steps].reverse().map((step, i) => {
          const isLatest = i === 0
          const tag = STEP_TAG[step.stepType]
          return (
            <div
              key={step.id}
              className="animate-fade-slide-in"
              style={{
                padding: '10px 13px',
                borderRadius: 8,
                background: isLatest ? 'rgba(45,232,106,.05)' : 'var(--s2)',
                border: `1px solid ${isLatest ? 'rgba(45,232,106,.22)' : 'var(--b0)'}`,
                animationDelay: `${i * 0.03}s`,
                transition: 'border-color .22s var(--e-io), background .22s var(--e-io)',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.borderColor = 'var(--b1)' }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLElement).style.borderColor =
                  isLatest ? 'rgba(45,232,106,.22)' : 'var(--b0)'
              }}
            >
              {/* .ad-step-row */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                {tag && (
                  <span
                    style={{
                      fontFamily: 'var(--fd)',
                      fontSize: 11,
                      padding: '1px 6px',
                      borderRadius: 3,
                      color: tag.color,
                      background: tag.bg,
                      flexShrink: 0,
                      letterSpacing: '0.02em',
                    }}
                  >
                    {tag.label}
                  </span>
                )}
                <span
                  style={{
                    fontFamily: 'var(--fd)',
                    fontSize: 11,
                    color: 'var(--tf)',
                    flexShrink: 0,
                  }}
                >
                  step {step.stepNumber}
                </span>
                <span
                  style={{
                    fontFamily: 'var(--fd)',
                    fontSize: 14,
                    color: isLatest ? 'var(--tp)' : 'var(--tm)',
                    flex: 1,
                    lineHeight: 1.4,
                  }}
                >
                  {step.description}
                </span>
              </div>
              {/* .ad-step-meta */}
              <div
                style={{
                  display: 'flex',
                  gap: 14,
                  marginTop: 5,
                  paddingTop: 5,
                  borderTop: '1px solid var(--b0)',
                }}
              >
                <span style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)' }}>
                  {step.durationMs > 0 ? `${step.durationMs}ms` : '—'}
                </span>
              </div>
            </div>
          )
        })}
      </div>

      {/* .ad-right — metrics sidebar */}
      <div
        style={{
          width: 300,
          flexShrink: 0,
          overflowY: 'auto',
          padding: '22px 24px 22px 20px',
          display: 'flex',
          flexDirection: 'column' as const,
          gap: 16,
          background: 'var(--s1)',
        }}
      >
        <div>
          <div
            style={{
              fontFamily: 'var(--fh)',
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.08em',
              textTransform: 'uppercase' as const,
              color: 'var(--tm)',
              marginBottom: 8,
            }}
          >
            Metriche
          </div>

          {[
            { label: 'Status',      value: agent?.status?.toUpperCase() ?? 'IDLE',   accent: agent?.status === 'running' },
            { label: 'Step totali', value: String(steps.length),                     accent: false },
            { label: 'Ultimo step', value: steps[steps.length - 1]?.stepType ?? '—', accent: false },
          ].map((row) => (
            <div
              key={row.label}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '6px 0',
                borderBottom: '1px solid var(--b0)',
                transition: 'background .18s',
              }}
              onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(45,232,106,.03)' }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
            >
              <span style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tm)' }}>
                {row.label}
              </span>
              <span
                style={{
                  fontFamily: 'var(--fd)',
                  fontSize: 13,
                  color: row.accent ? 'var(--accent)' : 'var(--tp)',
                }}
              >
                {row.value}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
