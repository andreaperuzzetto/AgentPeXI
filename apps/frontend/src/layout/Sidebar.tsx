/**
 * Sidebar — FE-1.4
 *
 * Sidebar verticale fissa a sinistra (64px).
 * Ogni item: icona Lucide + label sotto + indicatore attivo (bar sinistra
 * colorata con layoutId Framer Motion — fluida tra le zone, stile Kimi).
 *
 * Skill directives applicati:
 * - `motion.div layoutId="activeBar"` — barra colorata condivisa, si anima
 *   automaticamente tra le voci senza CSS transition statica
 * - `whileHover={{ x: 2 }}` spring su ogni button — feedback tattile micro
 */
import { motion } from 'framer-motion'
import type { LucideIcon } from 'lucide-react'
import {
  Brain,
  Store,
  User,
  Cpu,
  BarChart3,
} from 'lucide-react'
import { useStore } from '../store'

// ─── Zone config ──────────────────────────────────────────────────────────────
type Zone = 'neural' | 'etsy' | 'personal' | 'system' | 'analytics'

interface ZoneItem {
  id: Zone
  label: string
  icon: LucideIcon
  color: string
}

const ZONES: ZoneItem[] = [
  { id: 'neural',    label: 'Neural',    icon: Brain,     color: '#B57BFF' },
  { id: 'etsy',      label: 'Etsy',      icon: Store,     color: '#F5A623' },
  { id: 'personal',  label: 'Personal',  icon: User,      color: '#1BFF5E' },
  { id: 'system',    label: 'System',    icon: Cpu,       color: '#8B8D98' },
  { id: 'analytics', label: 'Analytics', icon: BarChart3, color: '#C8C8FF' },
]

// ─── Spring presets ───────────────────────────────────────────────────────────
const HOVER_SPRING   = { type: 'spring' as const, stiffness: 300, damping: 24 }
const ACTIVE_SPRING  = { type: 'spring' as const, stiffness: 380, damping: 32 }

// ─── Component ────────────────────────────────────────────────────────────────
export function Sidebar() {
  const activeZone    = useStore((s) => s.activeZone)
  const setActiveZone = useStore((s) => s.setActiveZone)

  return (
    <nav
      aria-label="Zone navigation"
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        width: 64,
        height: '100vh',
        background: 'var(--bg-s1)',
        borderRight: '1px solid var(--bs)',
        zIndex: 30,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        paddingTop: 16,
        paddingBottom: 16,
        gap: 4,
      }}
    >
      {/* ── Logo mark ── */}
      <div
        style={{
          width: 32,
          height: 32,
          borderRadius: 8,
          border: '1px solid rgba(181,123,255,.22)',
          background: 'rgba(181,123,255,.06)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          marginBottom: 20,
          flexShrink: 0,
        }}
      >
        <span style={{
          fontFamily: 'var(--fmo)',
          fontSize: 11,
          fontWeight: 700,
          color: '#B57BFF',
          letterSpacing: '-0.02em',
          lineHeight: 1,
        }}>
          P
        </span>
      </div>

      {/* ── Zone items ── */}
      {ZONES.map((zone) => {
        const isActive = activeZone === zone.id
        const Icon = zone.icon

        return (
          <motion.button
            key={zone.id}
            aria-label={`Go to ${zone.label}`}
            aria-current={isActive ? 'page' : undefined}
            onClick={() => setActiveZone(zone.id)}
            whileHover={{ x: 2 }}
            transition={HOVER_SPRING}
            style={{
              position: 'relative',
              width: 52,
              paddingTop: 8,
              paddingBottom: 8,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 4,
              borderRadius: 8,
              background: isActive
                ? `${zone.color}0f`   /* 6% tint */
                : 'transparent',
              border: 'none',
              cursor: 'pointer',
              outline: 'none',
              // Hardware-accelerated
              willChange: 'transform',
            }}
          >
            {/* ── Active bar — layoutId animates between zones ── */}
            {isActive && (
              <motion.div
                layoutId="activeBar"
                transition={ACTIVE_SPRING}
                style={{
                  position: 'absolute',
                  left: 0,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  width: 3,
                  height: 20,
                  borderRadius: '0 2px 2px 0',
                  background: zone.color,
                  boxShadow: `0 0 8px ${zone.color}66`,
                }}
              />
            )}

            {/* ── Icon — wrapper carries color/opacity; Lucide v1 doesn't accept style ── */}
            <span style={{
              color:      isActive ? zone.color : 'var(--tf)',
              opacity:    isActive ? 1 : 0.55,
              transition: 'color 0.15s, opacity 0.15s',
              display:    'flex',
              flexShrink: 0,
            }}>
              <Icon size={18} strokeWidth={isActive ? 2 : 1.5} />
            </span>

            {/* ── Label ── */}
            <span
              style={{
                fontFamily: 'var(--fmo)',
                fontSize: 9,
                fontWeight: 500,
                letterSpacing: '0.06em',
                textTransform: 'uppercase',
                color: isActive ? zone.color : 'var(--tf)',
                opacity: isActive ? 0.9 : 0.45,
                lineHeight: 1,
                transition: 'color 0.15s, opacity 0.15s',
                userSelect: 'none',
              }}
            >
              {zone.label}
            </span>
          </motion.button>
        )
      })}
    </nav>
  )
}
