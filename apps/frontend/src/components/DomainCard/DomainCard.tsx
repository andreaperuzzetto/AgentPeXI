import { useStore } from '../../store'

const AGENT_LIST = ['research', 'design', 'publisher', 'analytics']

export function DomainCard() {
  const setOverlaySystem = useStore((s) => s.setOverlaySystem)
  const agents = useStore((s) => s.agents)

  return (
    /* .sys-card */
    <div
        className="card"
        onClick={() => setOverlaySystem('etsy_store')}
        style={{ padding: '13px 14px', cursor: 'pointer' }}
        onMouseEnter={(e) => {
          const icon = e.currentTarget.querySelector('.sys-icon') as HTMLElement | null
          if (icon) {
            icon.style.borderColor = 'rgba(45,232,106,.25)'
            icon.style.boxShadow = '0 0 8px var(--aglow)'
          }
          const cta = e.currentTarget.querySelector('.sys-cta-text') as HTMLElement | null
          if (cta) cta.style.letterSpacing = '0.04em'
        }}
        onMouseLeave={(e) => {
          const icon = e.currentTarget.querySelector('.sys-icon') as HTMLElement | null
          if (icon) {
            icon.style.borderColor = 'var(--b0)'
            icon.style.boxShadow = 'none'
          }
          const cta = e.currentTarget.querySelector('.sys-cta-text') as HTMLElement | null
          if (cta) cta.style.letterSpacing = '0'
        }}
      >
        {/* Card header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* .sys-icon */}
          <div
            className="sys-icon"
            style={{
              width: 32,
              height: 32,
              borderRadius: 7,
              background: 'var(--s3)',
              border: '1px solid var(--b0)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontFamily: 'var(--fd)',
              fontSize: 9,
              color: 'var(--accent)',
              letterSpacing: '0.02em',
              flexShrink: 0,
              transition: 'border-color .25s var(--e-io), box-shadow .25s var(--e-io)',
            }}
          >
            ETY
          </div>

          <div>
            {/* .sys-name */}
            <div
              style={{
                fontFamily: 'var(--fh)',
                fontSize: 14,
                fontWeight: 700,
                letterSpacing: '0.04em',
                color: 'var(--tp)',
              }}
            >
              Etsy Store
            </div>
            {/* .sys-sub */}
            <div
              style={{
                fontFamily: 'var(--fd)',
                fontSize: 10,
                color: 'var(--tf)',
                marginTop: 2,
              }}
            >
              {AGENT_LIST.length} agenti nel sistema
            </div>
          </div>

          {/* .sys-badge */}
          <span
            style={{
              fontFamily: 'var(--fd)',
              fontSize: 9,
              color: 'var(--tf)',
              padding: '2px 8px',
              borderRadius: 99,
              border: '1px solid var(--b0)',
              marginLeft: 'auto',
              flexShrink: 0,
            }}
          >
            PENDING APPROVAL
          </span>
        </div>

        {/* Agent rows — .sys-agents */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 5,
            marginTop: 11,
            paddingTop: 10,
            borderTop: '1px solid var(--b0)',
          }}
        >
          {AGENT_LIST.map((name) => {
            const agent = agents[name]
            const isRunning = agent?.status === 'running'
            const isError   = agent?.status === 'error'
            return (
              <div key={name}>
                {/* .sys-agent-row */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
                  <span
                    className={isRunning ? 'status-dot status-dot--running' : 'status-dot'}
                    style={
                      isError ? { background: 'var(--err)' } :
                      !isRunning ? { background: 'var(--tf)' } :
                      undefined
                    }
                  />
                  {/* .aname-sm */}
                  <span
                    style={{
                      fontFamily: 'var(--fd)',
                      fontSize: 12,
                      color: 'var(--tm)',
                      flex: 1,
                      letterSpacing: '0.02em',
                      textTransform: 'uppercase' as const,
                    }}
                  >
                    {name}
                  </span>
                  {/* .astatus-sm */}
                  <span
                    style={{
                      fontFamily: 'var(--fd)',
                      fontSize: 10,
                      padding: '1px 7px',
                      borderRadius: 99,
                      border: `1px solid ${
                        isRunning ? 'rgba(45,232,106,.25)' :
                        isError   ? 'rgba(224,82,82,.25)' :
                        'var(--b0)'
                      }`,
                      color: isRunning ? 'var(--accent)' : isError ? 'var(--err)' : 'var(--tf)',
                      transition: 'color .2s, border-color .2s',
                    }}
                  >
                    {agent?.status?.toUpperCase() ?? 'IDLE'}
                  </span>
                </div>
                {/* .atask-preview */}
                {isRunning && agent?.lastTask && (
                  <div
                    style={{
                      fontFamily: 'var(--fd)',
                      fontSize: 10,
                      color: 'var(--tf)',
                      paddingLeft: 14,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap' as const,
                      marginTop: 1,
                    }}
                  >
                    {agent.lastTask}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {/* CTA — .sys-cta */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            marginTop: 10,
            paddingTop: 9,
            borderTop: '1px solid var(--b0)',
          }}
        >
          <span
            className="sys-cta-text"
            style={{
              fontFamily: 'var(--fd)',
              fontSize: 10,
              color: 'var(--accent)',
              transition: 'letter-spacing .2s var(--e-out)',
            }}
          >
            Dettaglio agenti e reasoning →
          </span>
        </div>
      </div>
  )
}
