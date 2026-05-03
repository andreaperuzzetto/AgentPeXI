/**
 * Sidebar — floating glass card, inset 12px da tutti i bordi viewport.
 * Nessun full-bleed: border-radius su tutti e 4 gli angoli (stile app).
 */
import { motion } from 'framer-motion'
import { useStore } from '../store'

// ─── Zone config ──────────────────────────────────────────────────────────────
type Zone = 'neural' | 'etsy' | 'personal' | 'system' | 'analytics'

interface ZoneItem {
  id:    Zone
  label: string
  color: string
}

const ZONES: ZoneItem[] = [
  { id: 'neural',    label: 'Neural',    color: '#8B7CF6' },
  { id: 'etsy',      label: 'Etsy',      color: '#F59E0B' },
  { id: 'personal',  label: 'Personal',  color: '#4ADE80' },
  { id: 'system',    label: 'System',    color: '#6B7280' },
  { id: 'analytics', label: 'Analytics', color: '#94A3B8' },
]

// ─── Custom SVG icons — strokeWidth 1.6 inactive, 2.0 active ─────────────────

function IconNeural({ color, active }: { color: string; active: boolean }) {
  const sw = active ? 2.0 : 1.6
  return (
    <svg viewBox="0 0 20 20" width="24" height="24" fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="10" r="2.2" />
      <circle cx="4"  cy="5"  r="1.2" />
      <circle cx="16" cy="5"  r="1.2" />
      <circle cx="16" cy="15" r="1.2" />
      <circle cx="4"  cy="15" r="1.2" />
      <line x1="8.2"  y1="8.4"  x2="5.1"  y2="6.2" />
      <line x1="11.8" y1="8.4"  x2="14.9" y2="6.2" />
      <line x1="11.8" y1="11.6" x2="14.9" y2="13.8" />
      <line x1="8.2"  y1="11.6" x2="5.1"  y2="13.8" />
    </svg>
  )
}

function IconEtsy({ color, active }: { color: string; active: boolean }) {
  const sw = active ? 2.0 : 1.6
  return (
    <svg viewBox="0 0 20 20" width="24" height="24" fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
      <path d="M11.5 3H16.5V8L10 14.5C9.2 15.3 7.9 15.3 7.1 14.5L5.5 12.9C4.7 12.1 4.7 10.8 5.5 10L11.5 4V3Z" />
      <circle cx="14" cy="6" r="1.1" fill={color} strokeWidth="0" />
    </svg>
  )
}

function IconPersonal({ color, active }: { color: string; active: boolean }) {
  const sw = active ? 2.0 : 1.6
  return (
    <svg viewBox="0 0 20 20" width="24" height="24" fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10" cy="7" r="3.2" />
      <path d="M3 18C3 14.5 6.1 11.8 10 11.8C13.9 11.8 17 14.5 17 18" />
    </svg>
  )
}

function IconSystem({ color, active }: { color: string; active: boolean }) {
  const sw = active ? 2.0 : 1.6
  return (
    <svg viewBox="0 0 20 20" width="24" height="24" fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
      <rect x="5.5" y="5.5" width="9" height="9" rx="1.5" />
      <circle cx="10" cy="10" r="2.2" />
      <line x1="8"  y1="5.5" x2="8"  y2="3" />
      <line x1="12" y1="5.5" x2="12" y2="3" />
      <line x1="8"  y1="14.5" x2="8"  y2="17" />
      <line x1="12" y1="14.5" x2="12" y2="17" />
      <line x1="5.5" y1="8"  x2="3" y2="8" />
      <line x1="5.5" y1="12" x2="3" y2="12" />
      <line x1="14.5" y1="8"  x2="17" y2="8" />
      <line x1="14.5" y1="12" x2="17" y2="12" />
    </svg>
  )
}

function IconAnalytics({ color, active }: { color: string; active: boolean }) {
  const sw = active ? 2.0 : 1.6
  return (
    <svg viewBox="0 0 20 20" width="24" height="24" fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="2,15 6,9 10,12 14,5.5 18,8" />
      <circle cx="6"  cy="9"   r="1.3" fill={color} strokeWidth="0" />
      <circle cx="10" cy="12"  r="1.3" fill={color} strokeWidth="0" />
      <circle cx="14" cy="5.5" r="1.3" fill={color} strokeWidth="0" />
      <line x1="2" y1="17" x2="18" y2="17" strokeOpacity="0.25" />
    </svg>
  )
}

const ICON_MAP: Record<Zone, (props: { color: string; active: boolean }) => JSX.Element> = {
  neural:    IconNeural,
  etsy:      IconEtsy,
  personal:  IconPersonal,
  system:    IconSystem,
  analytics: IconAnalytics,
}

const HOVER_SPRING  = { type: 'spring' as const, stiffness: 300, damping: 24 }
const ACTIVE_SPRING = { type: 'spring' as const, stiffness: 380, damping: 32 }

// ─── Sidebar ──────────────────────────────────────────────────────────────────
export function Sidebar() {
  const activeZone    = useStore((s) => s.activeZone)
  const setActiveZone = useStore((s) => s.setActiveZone)

  return (
    <nav
      aria-label="Zone navigation"
      style={{
        position:       'fixed',
        top:            12,
        left:           12,
        width:          118,
        height:         'calc(100vh - 24px)',
        borderRadius:   16,
        background:     'rgba(255,255,255,0.07)',
        backdropFilter: 'blur(32px)',
        WebkitBackdropFilter: 'blur(32px)',
        border:         '1px solid rgba(255,255,255,0.09)',
        boxShadow:      '0 8px 40px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.10)',
        zIndex:         30,
        display:        'flex',
        flexDirection:  'column',
        alignItems:     'center',
        paddingBottom:  16,
        gap:            2,
        overflow:       'hidden',
      }}
    >
      {/* ── Logo ── */}
      <div style={{
        width:          '100%',
        display:        'flex',
        flexDirection:  'column',
        alignItems:     'center',
        justifyContent: 'center',
        gap:            8,
        padding:        '22px 0 18px',
        borderBottom:   '1px solid rgba(255,255,255,0.06)',
        flexShrink:     0,
      }}>
        <svg viewBox="0 0 36 36" width="42" height="42" fill="none">
          <defs>
            <linearGradient id="logo-a" x1="18" y1="4" x2="18" y2="34" gradientUnits="userSpaceOnUse">
              <stop offset="0%"   stopColor="#5ab4f5"/>
              <stop offset="100%" stopColor="#2260c0"/>
            </linearGradient>
          </defs>
          {/* Clean geometric A */}
          <path d="M 6 32 L 18 6 L 30 32" stroke="url(#logo-a)" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
          <path d="M 10.8 21.5 L 25.2 21.5"  stroke="url(#logo-a)" strokeWidth="3.2" strokeLinecap="round" fill="none"/>
        </svg>

        <span style={{
          fontFamily:    'var(--fui)',
          fontSize:      10,
          fontWeight:    700,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color:         'rgba(255,255,255,0.55)',
          lineHeight:    1,
          userSelect:    'none',
        }}>
          AgentPeXI
        </span>
      </div>

      {/* ── Zone nav items ── */}
      <div style={{
        flex:           1,
        width:          '100%',
        display:        'flex',
        flexDirection:  'column',
        alignItems:     'center',
        padding:        '10px 0',
        gap:            2,
        overflowY:      'auto',
        scrollbarWidth: 'none',
      }}>
        {ZONES.map((zone) => {
          const isActive = activeZone === zone.id
          const IconComp = ICON_MAP[zone.id]

          return (
            <motion.button
              key={zone.id}
              aria-label={`Go to ${zone.label}`}
              aria-current={isActive ? 'page' : undefined}
              onClick={() => setActiveZone(zone.id)}
              whileHover={!isActive ? { scale: 1.02 } : {}}
              transition={HOVER_SPRING}
              style={{
                position:       'relative',
                width:          94,
                paddingTop:     12,
                paddingBottom:  10,
                display:        'flex',
                flexDirection:  'column',
                alignItems:     'center',
                gap:            6,
                borderRadius:   10,
                background:     'transparent',
                border:         'none',
                cursor:         'pointer',
                outline:        'none',
                willChange:     'transform',
              }}
            >
              {/* Active pill background */}
              {isActive && (
                <motion.div
                  layoutId="activePill"
                  transition={ACTIVE_SPRING}
                  style={{
                    position:     'absolute',
                    inset:        0,
                    borderRadius: 10,
                    background:   `${zone.color}18`,
                    border:       `1px solid ${zone.color}30`,
                    boxShadow:    `inset 0 1px 0 ${zone.color}14`,
                  }}
                />
              )}

              {/* Icon */}
              <span style={{
                opacity:    isActive ? 1 : 0.45,
                transition: 'opacity 0.15s',
                display:    'flex',
                flexShrink: 0,
                position:   'relative',
              }}>
                <IconComp color={zone.color} active={isActive} />
              </span>

              {/* Label */}
              <span style={{
                fontFamily:    'var(--fui)',
                fontSize:      11,
                fontWeight:    isActive ? 600 : 500,
                letterSpacing: '0.02em',
                color:         isActive ? zone.color : 'rgba(255,255,255,0.42)',
                lineHeight:    1,
                transition:    'color 0.15s',
                userSelect:    'none',
                position:      'relative',
              }}>
                {zone.label}
              </span>
            </motion.button>
          )
        })}
      </div>
    </nav>
  )
}
