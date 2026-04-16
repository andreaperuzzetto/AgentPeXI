import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../../store'
import { AgentOverlayCard } from './AgentOverlayCard'
import { AgentDetailPanel } from './AgentDetailPanel'

const SECTION_CONFIG: Record<string, {
  title: string
  badge?: string
  agents: string[]
}> = {
  etsy_store: {
    title:  'Etsy Store',
    badge:  'PENDING APPROVAL',
    agents: ['research', 'design', 'publisher', 'analytics'],
  },
  personal: {
    title:  'Personale',
    badge:  'LOCALE · OLLAMA',
    agents: ['recall', 'watcher'],
  },
}

export function SystemOverlay() {
  const overlaySystem    = useStore((s) => s.overlaySystem)
  const setOverlaySystem = useStore((s) => s.setOverlaySystem)
  const selectedAgent    = useStore((s) => s.selectedAgent)
  const setSelectedAgent = useStore((s) => s.setSelectedAgent)

  const section = overlaySystem ? (SECTION_CONFIG[overlaySystem] ?? SECTION_CONFIG['etsy_store']) : null

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (selectedAgent) setSelectedAgent(null)
        else setOverlaySystem(null)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [setOverlaySystem, selectedAgent, setSelectedAgent])

  if (!overlaySystem) return null

  return createPortal(
    /* .modal-backdrop */
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 50,
        background: 'rgba(8,14,10,.82)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        animation: 'backdrop-in .28s var(--e-io) both',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) setOverlaySystem(null) }}
    >
      {/* .modal */}
      <div
        style={{
          position: 'relative',
          width: 'min(980px, 90vw)',
          height: 'min(740px, 88vh)',
          background: 'var(--s1)',
          border: '1px solid var(--b1)',
          borderRadius: 18,
          boxShadow: `
            0 0 0 1px rgba(45,232,106,.06),
            0 32px 100px rgba(0,0,0,.8),
            0 8px 32px rgba(0,0,0,.5),
            inset 0 1px 0 rgba(255,255,255,.045)
          `,
          display: 'flex',
          flexDirection: 'column' as const,
          overflow: 'hidden',
          animation: 'modal-in .3s var(--e-out) both',
          willChange: 'transform, opacity',
        }}
      >
        {/* .modal-head */}
        <div
          style={{
            height: 56,
            flexShrink: 0,
            background: 'var(--s1)',
            borderBottom: '1px solid var(--b0)',
            display: 'flex',
            alignItems: 'center',
            padding: '0 22px',
            gap: 12,
          }}
        >
          {/* Back button — visible when agent is selected */}
          {selectedAgent && (
            <button
              onClick={() => setSelectedAgent(null)}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--tm)',
                fontFamily: 'var(--fb)',
                fontSize: 15,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: 0,
                transition: 'color .22s var(--e-io), transform .22s var(--e-out)',
              }}
              onMouseEnter={(e) => {
                const el = e.currentTarget as HTMLElement
                el.style.color = 'var(--tp)'
                el.style.transform = 'translateX(-2px)'
              }}
              onMouseLeave={(e) => {
                const el = e.currentTarget as HTMLElement
                el.style.color = 'var(--tm)'
                el.style.transform = 'translateX(0)'
              }}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 14 14"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M9 2L4 7l5 5" />
              </svg>
              Indietro
            </button>
          )}

          {/* Title */}
          <span
            style={{
              fontFamily: 'var(--fh)',
              fontSize: 20,
              fontWeight: 800,
              letterSpacing: '0.03em',
              color: 'var(--tp)',
            }}
          >
            {section?.title ?? '—'}
          </span>
          {section?.badge && (
            <span
              style={{
                fontFamily: 'var(--fd)',
                fontSize: 12,
                color: 'var(--tf)',
                padding: '2px 9px',
                borderRadius: 99,
                border: '1px solid var(--b0)',
              }}
            >
              {section.badge}
            </span>
          )}

          {/* Close button */}
          <button
            onClick={() => setOverlaySystem(null)}
            style={{
              marginLeft: 'auto',
              background: 'none',
              border: '1px solid var(--b0)',
              borderRadius: 7,
              padding: '6px 14px',
              color: 'var(--tm)',
              cursor: 'pointer',
              fontFamily: 'var(--fb)',
              fontSize: 15,
              transition: 'border-color .25s var(--e-io), color .25s var(--e-io), transform .2s var(--e-spring)',
            }}
            onMouseEnter={(e) => {
              const el = e.currentTarget as HTMLElement
              el.style.borderColor = 'var(--b1)'
              el.style.color = 'var(--tp)'
              el.style.transform = 'scale(1.03)'
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget as HTMLElement
              el.style.borderColor = 'var(--b0)'
              el.style.color = 'var(--tm)'
              el.style.transform = 'scale(1)'
            }}
          >
            ✕ Chiudi
          </button>
        </div>

        {/* Body — .sys-modal-pages */}
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden', position: 'relative' }}>

          {/* .page-grid — agent cards */}
          <div
            style={{
              flex: 1,
              overflow: 'auto',
              padding: 22,
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(210px, 1fr))',
              gap: 14,
              alignContent: 'start',
              transition: 'transform .38s var(--e-out), opacity .28s var(--e-io)',
              ...(selectedAgent
                ? { transform: 'translateX(-55%)', opacity: 0, pointerEvents: 'none' as const }
                : {}),
            }}
          >
            {(section?.agents ?? []).map((name, i) => (
              <AgentOverlayCard key={name} agentName={name} index={i} />
            ))}
          </div>

          {/* .page-detail — agent detail panel */}
          <AgentDetailPanel />
        </div>
      </div>
    </div>,
    document.body,
  )
}
