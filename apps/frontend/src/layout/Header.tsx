/**
 * Header — FE-1.5
 *
 * Layout fisso (56px, left 64):
 *   [AgentPeXI · zone-badge]  ── spacer ──  [● autopilot pill]  ── spacer ──  [LLM img fee bars]  [BRIEF]  [mock ⦿]  [WS ●]
 *
 * Skill directives applicati:
 * - Autopilot pill: struttura Double-Bezel (outer ring + inner pill)
 * - Il colore dell'outer ring segue --zone-* della zona attiva
 * - Magnetic button physics su autopilot pill (useMotionValue Framer Motion)
 * - spring { stiffness: 300, damping: 25 } su x/y — quasi-invisibile ma percepibile
 */
import { useEffect } from 'react'
import { motion, useMotionValue, useSpring } from 'framer-motion'
import { useShallow } from 'zustand/react/shallow'
import { useStore } from '../store'

// ─── Zone config ──────────────────────────────────────────────────────────────
type Zone = 'neural' | 'etsy' | 'personal' | 'system' | 'analytics'

const ZONE_COLORS: Record<Zone, string> = {
  neural:    '#B57BFF',
  etsy:      '#F5A623',
  personal:  '#1BFF5E',
  system:    '#8B8D98',
  analytics: '#C8C8FF',
}

const ZONE_LABELS: Record<Zone, string> = {
  neural:    'Neural',
  etsy:      'Etsy',
  personal:  'Personal',
  system:    'System',
  analytics: 'Analytics',
}

// ─── Sub-components ───────────────────────────────────────────────────────────

/** Budget mini bar — label + colored fill */
function BudgetBar({
  label,
  pct,
  color,
}: {
  label: string
  pct: number
  color: string
}) {
  const clamped = Math.min(pct, 100)
  const warn    = clamped > 80
  const fill    = warn ? 'var(--err)' : color

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, alignItems: 'center', minWidth: 32 }}>
      <span style={{
        fontFamily: 'var(--fmo)',
        fontSize: 8,
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
        color: warn ? 'var(--err)' : 'var(--tf)',
        opacity: warn ? 1 : 0.55,
        lineHeight: 1,
      }}>
        {label}
      </span>
      <div style={{
        width: 32,
        height: 3,
        borderRadius: 2,
        background: 'rgba(255,255,255,0.06)',
        overflow: 'hidden',
      }}>
        <motion.div
          style={{ height: '100%', borderRadius: 2, background: fill, transformOrigin: 'left' }}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: clamped / 100 }}
          transition={{ type: 'spring', stiffness: 80, damping: 18 }}
        />
      </div>
    </div>
  )
}

/** Magnetic autopilot pill — Double-Bezel + physics */
function AutopilotPill({
  status,
  niche,
  zoneColor,
  onToggle,
}: {
  status: 'running' | 'paused' | 'stopped'
  niche: string | null
  zoneColor: string
  onToggle: () => void
}) {
  // ── Magnetic physics ──
  const mx = useMotionValue(0)
  const my = useMotionValue(0)
  const sx = useSpring(mx, { stiffness: 300, damping: 25 })
  const sy = useSpring(my, { stiffness: 300, damping: 25 })

  const onMouseMove = (e: React.MouseEvent<HTMLButtonElement>) => {
    const r  = e.currentTarget.getBoundingClientRect()
    const cx = r.left + r.width  / 2
    const cy = r.top  + r.height / 2
    mx.set((e.clientX - cx) * 0.35)
    my.set((e.clientY - cy) * 0.35)
  }
  const onMouseLeave = () => { mx.set(0); my.set(0) }

  // ── Colors by status ──
  const dotColor   = status === 'running' ? 'var(--ok)' : status === 'paused' ? 'var(--err)' : 'var(--tf)'
  const labelColor = status === 'running' ? 'var(--ok)' : status === 'paused' ? 'var(--err)' : 'var(--tf)'
  const statusText = status === 'running' ? 'Running' : status === 'paused' ? 'Paused' : 'Stopped'

  return (
    /* ── Double-Bezel outer shell ── */
    <div style={{
      padding: 2,
      borderRadius: 99,
      border: `1px solid ${zoneColor}28`,
      background: `${zoneColor}06`,
    }}>
      {/* ── Double-Bezel inner core ── */}
      <motion.button
        style={{
          x: sx,
          y: sy,
          display:        'flex',
          alignItems:     'center',
          gap:            7,
          padding:        '5px 12px',
          borderRadius:   99,
          border:         '1px solid rgba(255,255,255,0.07)',
          background:     'var(--bg-s1)',
          boxShadow:      'inset 0 1px 0 rgba(255,255,255,0.08)',
          cursor:         'pointer',
          outline:        'none',
          userSelect:     'none',
          willChange:     'transform',
        }}
        whileTap={{ scale: 0.97 }}
        transition={{ type: 'spring', stiffness: 300, damping: 25 }}
        onMouseMove={onMouseMove}
        onMouseLeave={onMouseLeave}
        onClick={onToggle}
        aria-label={`Autopilot is ${statusText}. Click to ${status === 'running' ? 'pause' : 'start'}.`}
      >
        {/* Status dot */}
        <span style={{
          width:        6,
          height:       6,
          borderRadius: '50%',
          background:   dotColor,
          flexShrink:   0,
          boxShadow:    status === 'running' ? `0 0 6px ${dotColor}` : 'none',
          animation:    status === 'running' ? 'pdot 2s ease-in-out infinite' : 'none',
          display:      'block',
        }} />

        {/* Label */}
        <span style={{
          fontFamily:     'var(--fmo)',
          fontSize:       11,
          fontWeight:     500,
          letterSpacing:  '0.06em',
          color:          labelColor,
          lineHeight:     1,
          textTransform:  'uppercase',
        }}>
          {statusText}
        </span>

        {/* Niche tag — shown when running */}
        {status === 'running' && niche && (
          <span style={{
            fontFamily:     'var(--fmo)',
            fontSize:       10,
            color:          'var(--tf)',
            opacity:        0.7,
            maxWidth:       80,
            overflow:       'hidden',
            textOverflow:   'ellipsis',
            whiteSpace:     'nowrap',
            letterSpacing:  '0.03em',
          }}>
            {niche}
          </span>
        )}
      </motion.button>
    </div>
  )
}

/** Mock mode toggle switch */
function MockToggle({ enabled, onToggle }: { enabled: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      title={`Mock mode: ${enabled ? 'ON' : 'OFF'}`}
      aria-label={`Mock mode ${enabled ? 'enabled' : 'disabled'}`}
      style={{
        display:        'flex',
        alignItems:     'center',
        gap:            5,
        padding:        '3px 8px',
        borderRadius:   4,
        border:         `1px solid ${enabled ? 'rgba(245,166,35,.28)' : 'var(--bs)'}`,
        background:     enabled ? 'rgba(245,166,35,.06)' : 'transparent',
        cursor:         'pointer',
        outline:        'none',
        transition:     'border-color 0.15s, background 0.15s',
      }}
    >
      {/* Track */}
      <div style={{
        width:          24,
        height:         12,
        borderRadius:   6,
        background:     enabled ? 'rgba(245,166,35,.35)' : 'rgba(255,255,255,.08)',
        position:       'relative',
        transition:     'background 0.2s',
        flexShrink:     0,
      }}>
        <motion.div
          animate={{ x: enabled ? 13 : 1 }}
          transition={{ type: 'spring', stiffness: 400, damping: 28 }}
          style={{
            position:     'absolute',
            top:          2,
            width:        8,
            height:       8,
            borderRadius: '50%',
            background:   enabled ? 'var(--wrn)' : 'var(--tf)',
          }}
        />
      </div>
      <span style={{
        fontFamily:    'var(--fmo)',
        fontSize:      9,
        letterSpacing: '0.1em',
        textTransform: 'uppercase',
        color:         enabled ? 'var(--wrn)' : 'var(--tf)',
        opacity:       enabled ? 1 : 0.45,
        lineHeight:    1,
      }}>
        mock
      </span>
    </button>
  )
}

/** Brief button — opens ContextOverlay */
function BriefButton({ onClick }: { onClick: () => void }) {
  return (
    <motion.button
      onClick={onClick}
      whileHover={{ x: 1 }}
      whileTap={{ scale: 0.97 }}
      transition={{ type: 'spring', stiffness: 300, damping: 24 }}
      style={{
        fontFamily:    'var(--fmo)',
        fontSize:      10,
        fontWeight:    500,
        letterSpacing: '0.16em',
        textTransform: 'uppercase',
        color:         'var(--tf)',
        padding:       '4px 10px',
        borderRadius:  4,
        border:        '1px solid var(--bs)',
        background:    'transparent',
        cursor:        'pointer',
        outline:       'none',
        transition:    'border-color 0.15s, color 0.15s',
        willChange:    'transform',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = 'rgba(181,123,255,.28)'
        e.currentTarget.style.color       = 'rgba(181,123,255,.8)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = 'var(--bs)'
        e.currentTarget.style.color       = 'var(--tf)'
      }}
      aria-label="Open context brief"
    >
      [ BRIEF ]
    </motion.button>
  )
}

// ─── Main Header component ────────────────────────────────────────────────────
export function Header() {
  const {
    wsConnected,
    activeZone,
    autopilotStatus,
    autopilotCurrentNiche,
    llmStats,
    budgetMonthlyUsd,
    imageCostToday,
    feeCostToday,
    systemStatus,
    setAutopilotStatus,
    setBriefOpen,
  } = useStore(
    useShallow((s) => ({
      wsConnected:          s.wsConnected,
      activeZone:           s.activeZone,
      autopilotStatus:      s.autopilotStatus,
      autopilotCurrentNiche: s.autopilotCurrentNiche,
      llmStats:             s.llmStats,
      budgetMonthlyUsd:     s.budgetMonthlyUsd,
      imageCostToday:       s.imageCostToday,
      feeCostToday:         s.feeCostToday,
      systemStatus:         s.systemStatus,
      setAutopilotStatus:   s.setAutopilotStatus,
      setBriefOpen:         s.setBriefOpen,
    }))
  )

  const zoneColor  = ZONE_COLORS[activeZone as Zone] ?? '#B57BFF'
  const zoneLabel  = ZONE_LABELS[activeZone as Zone] ?? activeZone

  // ── Mock mode local toggle ─────────────────────────────────────────────────
  const mockEnabled = (systemStatus as { mock_mode?: boolean }).mock_mode ?? false

  const toggleMock = () => {
    fetch('/api/system/mock', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ enabled: !mockEnabled }),
    }).catch(() => {})
  }

  // ── Autopilot fetch on mount ───────────────────────────────────────────────
  useEffect(() => {
    fetch('/api/autopilot/status')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.status) setAutopilotStatus(data.status, data.current_niche ?? null)
      })
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Autopilot toggle ───────────────────────────────────────────────────────
  const handleAutopilotToggle = () => {
    if (autopilotStatus === 'running') {
      fetch('/api/autopilot/pause', { method: 'POST' })
        .then((r) => r.ok ? setAutopilotStatus('paused') : null)
        .catch(() => {})
    } else {
      fetch('/api/autopilot/start', { method: 'POST' })
        .then((r) => r.ok ? setAutopilotStatus('running') : null)
        .catch(() => {})
    }
  }

  // ── Budget percentages ─────────────────────────────────────────────────────
  const budget     = budgetMonthlyUsd ?? 50          // fallback $50
  const llmPct     = (llmStats.runCost / budget) * 100
  // Image & fee: usa proportion giornaliera relativa a 1/30 del budget mensile
  const dailySlice = budget / 30
  const imgPct     = dailySlice > 0 ? (imageCostToday  / dailySlice) * 100 : 0
  const feePct     = dailySlice > 0 ? (feeCostToday    / dailySlice) * 100 : 0

  return (
    <div style={{
      position:     'fixed',
      top:          0,
      left:         64,
      right:        0,
      height:       56,
      zIndex:       20,
      display:      'flex',
      alignItems:   'center',
      padding:      '0 16px',
      gap:          10,
      background:   'var(--bg-s1)',
      borderBottom: '1px solid var(--bs)',
    }}>

      {/* ── Left: logo + zone badge ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <span style={{
          fontFamily:    'var(--fui)',
          fontWeight:    600,
          fontSize:      13,
          letterSpacing: '-0.02em',
          color:         'var(--tp)',
          lineHeight:    1,
        }}>
          AgentPeXI
        </span>

        <span style={{ color: 'var(--bs)', fontSize: 14, opacity: 0.6, userSelect: 'none' }}>·</span>

        {/* Zone badge — colored pill */}
        <span style={{
          fontFamily:    'var(--fmo)',
          fontSize:      10,
          fontWeight:    500,
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color:         zoneColor,
          padding:       '2px 8px',
          borderRadius:  99,
          border:        `1px solid ${zoneColor}30`,
          background:    `${zoneColor}0a`,
          lineHeight:    1.4,
          flexShrink:    0,
        }}>
          {zoneLabel}
        </span>
      </div>

      {/* ── Spacer ── */}
      <div style={{ flex: 1 }} />

      {/* ── Center: autopilot pill ── */}
      <AutopilotPill
        status={autopilotStatus}
        niche={autopilotCurrentNiche}
        zoneColor={zoneColor}
        onToggle={handleAutopilotToggle}
      />

      {/* ── Spacer ── */}
      <div style={{ flex: 1 }} />

      {/* ── Right: budget bars ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <BudgetBar label="LLM" pct={llmPct} color={zoneColor} />
        <BudgetBar label="img" pct={imgPct} color={zoneColor} />
        <BudgetBar label="fee" pct={feePct} color={zoneColor} />
      </div>

      {/* ── Separator ── */}
      <div style={{ width: 1, height: 20, background: 'var(--bs)', flexShrink: 0 }} />

      {/* ── Brief button ── */}
      <BriefButton onClick={() => setBriefOpen(true)} />

      {/* ── Mock toggle ── */}
      <MockToggle enabled={mockEnabled} onToggle={toggleMock} />

      {/* ── WS dot ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
        <span style={{
          width:        6,
          height:       6,
          borderRadius: '50%',
          background:   wsConnected ? 'var(--ok)' : 'var(--tf)',
          display:      'block',
          flexShrink:   0,
          boxShadow:    wsConnected ? '0 0 7px rgba(27,255,94,.7)' : 'none',
          animation:    wsConnected ? 'pdot 2.4s ease-in-out infinite' : 'none',
        }} />
        <span style={{
          fontFamily:    'var(--fmo)',
          fontSize:      10,
          letterSpacing: '0.06em',
          color:         wsConnected ? 'var(--ok)' : 'var(--tf)',
          opacity:       wsConnected ? 0.8 : 0.4,
          lineHeight:    1,
          textTransform: 'uppercase',
        }}>
          {wsConnected ? 'live' : 'off'}
        </span>
      </div>

    </div>
  )
}
