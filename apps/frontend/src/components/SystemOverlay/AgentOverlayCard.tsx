import { useStore } from '../../store'

const EMPTY_STEPS: never[] = []

const AGENT_DESCS: Record<string, string> = {
  research:  'Analisi di mercato, ricerca nicchie, trend Etsy e dati competitivi.',
  design:    'Generazione immagini, prompt engineering, output SVG e PNG ad alta risoluzione.',
  publisher: 'Creazione listing, titoli SEO, tag e pubblicazione su Etsy.',
  analytics: 'Monitoraggio KPI, A/B test, analisi performance e reportistica.',
}

interface Props {
  agentName: string
  index: number
}

export function AgentOverlayCard({ agentName, index }: Props) {
  const agent        = useStore((s) => s.agents[agentName])
  const steps        = useStore((s) => s.agentSteps[agentName] ?? EMPTY_STEPS)
  const selectedAgent = useStore((s) => s.selectedAgent)
  const setSelectedAgent = useStore((s) => s.setSelectedAgent)

  const isSelected = selectedAgent === agentName
  const isRunning  = agent?.status === 'running'
  const isError    = agent?.status === 'error'

  const statusColor =
    isRunning ? 'var(--accent)' :
    isError   ? 'var(--err)' :
    'var(--tf)'

  return (
    /* .ov-card */
    <div
      className="card animate-card-up"
      style={{
        padding: 16,
        cursor: 'pointer',
        animationDelay: `${index * 0.05}s`,
        ...(isSelected ? {
          borderColor: 'rgba(45,232,106,.4)',
          boxShadow: `
            inset 0 1px 0 rgba(255,255,255,.07),
            0 2px 8px rgba(0,0,0,.5),
            0 8px 28px rgba(0,0,0,.32),
            0 0 0 1px rgba(45,232,106,.1),
            0 0 32px rgba(45,232,106,.18)
          `,
        } : {}),
      }}
      onClick={() => setSelectedAgent(isSelected ? null : agentName)}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          className={isRunning ? 'status-dot status-dot--running' : 'status-dot'}
          style={
            isError ? { background: 'var(--err)' } :
            !isRunning ? { background: 'var(--tf)' } :
            undefined
          }
        />
        <span
          style={{
            fontFamily: 'var(--fh)',
            fontSize: 14,
            fontWeight: 700,
            letterSpacing: '0.05em',
            textTransform: 'uppercase' as const,
            color: 'var(--tp)',
            flex: 1,
          }}
        >
          {agentName}
        </span>
        {/* Status badge */}
        <span
          style={{
            fontFamily: 'var(--fd)',
            fontSize: 9,
            padding: '1px 8px',
            borderRadius: 99,
            border: `1px solid ${
              isRunning ? 'rgba(45,232,106,.28)' :
              isError   ? 'rgba(224,82,82,.28)' :
              'var(--b0)'
            }`,
            color: statusColor,
            letterSpacing: '0.04em',
          }}
        >
          {agent?.status?.toUpperCase() ?? 'IDLE'}
        </span>
      </div>

      {/* Description — .ov-card-desc */}
      <div
        style={{
          fontSize: 13,
          color: 'var(--tm)',
          marginTop: 6,
          lineHeight: 1.6,
        }}
      >
        {agent?.lastTask
          ? (agent.lastTask.length > 80 ? agent.lastTask.slice(0, 80) + '…' : agent.lastTask)
          : (AGENT_DESCS[agentName] ?? '')}
      </div>

      {/* Last steps preview */}
      {steps.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginTop: 8 }}>
          {steps.slice(-3).map((step, i) => {
            const isLatest = i === Math.min(2, steps.length - 1)
            return (
              <div
                key={step.id}
                style={{
                  fontFamily: 'var(--fd)',
                  fontSize: 11,
                  color: isLatest ? 'var(--tm)' : 'var(--tf)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap' as const,
                }}
              >
                {step.description.length > 52 ? step.description.slice(0, 52) + '…' : step.description}
              </div>
            )
          })}
        </div>
      )}

      {/* Footer — .ov-card-foot */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginTop: 11,
          paddingTop: 9,
          borderTop: '1px solid var(--b0)',
        }}
      >
        <span style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tf)' }}>
          {steps.length} step registrati
        </span>
        <span
          style={{
            fontFamily: 'var(--fd)',
            fontSize: 10,
            color: isSelected ? 'var(--tm)' : 'var(--accent)',
            transition: 'letter-spacing .2s var(--e-out)',
          }}
        >
          {isSelected ? 'Aperto ✓' : 'Dettaglio →'}
        </span>
      </div>
    </div>
  )
}
