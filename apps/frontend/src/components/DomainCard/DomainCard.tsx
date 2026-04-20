import { useState } from 'react'
import { useStore } from '../../store'

const ETSY_AGENTS    = ['research', 'design', 'publisher', 'analytics']
const PERSONAL_AGENTS = ['recall', 'remind', 'summarize', 'research_personal', 'watcher']

// Componente interno riusabile per una singola service card
function ServiceCard({
  sectionKey,
  label,
  abbr,
  title,
  subtitle,
  badge,
  agentList,
}: {
  sectionKey: string
  label: string
  abbr: string
  title: string
  subtitle: string
  badge: string
  agentList: string[]
}) {
  const setOverlaySystem = useStore((s) => s.setOverlaySystem)
  const agents = useStore((s) => s.agents)
  const [hovered, setHovered] = useState(false)

  return (
    <div
      className="card"
      onClick={() => setOverlaySystem(sectionKey)}
      style={{ padding: '13px 14px', cursor: 'pointer', flex: 1, minWidth: 0 }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Card header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div
          className="sys-icon"
          style={{
            width: 32,
            height: 32,
            borderRadius: 7,
            background: 'var(--s3)',
            border: `1px solid ${hovered ? 'rgba(45,232,106,.25)' : 'var(--b0)'}`,
            boxShadow: hovered ? '0 0 8px var(--aglow)' : 'none',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: 'var(--fd)',
            fontSize: 11,
            color: 'var(--accent)',
            letterSpacing: '0.02em',
            flexShrink: 0,
            transition: 'border-color .25s var(--e-io), box-shadow .25s var(--e-io)',
          }}
        >
          {abbr}
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--fh)', fontSize: 16, fontWeight: 700, letterSpacing: '0.04em', color: 'var(--tp)' }}>
            {title}
          </div>
          <div style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)', marginTop: 2 }}>
            {subtitle}
          </div>
        </div>
        <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)', padding: '2px 8px', borderRadius: 99, border: '1px solid var(--b0)', marginLeft: 'auto', flexShrink: 0 }}>
          {badge}
        </span>
      </div>

      {/* Agent rows */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 11, paddingTop: 10, borderTop: '1px solid var(--b0)' }}>
        {agentList.map((name) => {
          const agent = agents[name]
          const isRunning = agent?.status === 'running'
          const isError   = agent?.status === 'error'
          return (
            <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
              <span
                className={isRunning ? 'status-dot status-dot--running' : 'status-dot'}
                style={isError ? { background: 'var(--err)' } : !isRunning ? { background: 'var(--tf)' } : undefined}
              />
              <span style={{ fontFamily: 'var(--fd)', fontSize: 14, color: 'var(--tm)', flex: 1, letterSpacing: '0.02em', textTransform: 'uppercase' as const, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
                {name.replace('_', ' ')}
              </span>
              <span style={{ fontFamily: 'var(--fd)', fontSize: 12, padding: '1px 7px', borderRadius: 99, border: `1px solid ${isRunning ? 'rgba(45,232,106,.25)' : isError ? 'rgba(224,82,82,.25)' : 'var(--b0)'}`, color: isRunning ? 'var(--accent)' : isError ? 'var(--err)' : 'var(--tf)' }}>
                {agent?.status?.toUpperCase() ?? 'IDLE'}
              </span>
            </div>
          )
        })}
      </div>

      {/* CTA */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10, paddingTop: 9, borderTop: '1px solid var(--b0)' }}>
        <span className="sys-cta-text" style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--accent)', transition: 'letter-spacing .2s var(--e-out)', letterSpacing: hovered ? '0.04em' : '0' }}>
          {label} →
        </span>
      </div>
    </div>
  )
}

export function DomainCard() {
  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <ServiceCard
        sectionKey="etsy_store"
        label="Dettaglio agenti e reasoning"
        abbr="ETY"
        title="Etsy Store"
        subtitle={`${ETSY_AGENTS.length} agenti nel sistema`}
        badge="PENDING APPROVAL"
        agentList={ETSY_AGENTS}
      />
      <ServiceCard
        sectionKey="personal"
        label="Agenti e servizi personal"
        abbr="PSN"
        title="Personale"
        subtitle={`${PERSONAL_AGENTS.length} agenti · Ollama locale · €0`}
        badge="LOCALE"
        agentList={PERSONAL_AGENTS}
      />
    </div>
  )
}
