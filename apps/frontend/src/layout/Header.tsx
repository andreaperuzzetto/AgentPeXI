/**
 * Header — redesign premium HUD
 *
 * Layout fisso (56px, left 64):
 *   [AgentPeXI · zone-badge]  ── spacer ──  [● autopilot pill]  ── spacer ──  [LLM/img/fee bars]  [BRIEF]  [mock ⦿]  [WS ●]
 *
 * Redesign:
 * - BudgetBar: barre 5px con valore numerico visibile, alto contrasto
 * - AutopilotPill: pill colorato per status (RUNNING verde / STOPPED rosso / PAUSED ambra)
 * - Tutto il testo ad alto contrasto, nessun colore verdognolo
 */
import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { useShallow } from 'zustand/react/shallow'
import { useStore } from '../store'

// ─── AutopilotSegmented ───────────────────────────────────────────────────────
function AutopilotSegmented({
  status, onSet,
}: {
  status: 'running' | 'paused' | 'stopped'
  onSet:  (s: 'running' | 'paused' | 'stopped') => void
}) {
  const segments = [
    { id: 'running' as const, label: 'ACTIVE', color: '#2ECDB7' },
    { id: 'paused'  as const, label: 'PAUSE',  color: '#F59E0B' },
    { id: 'stopped' as const, label: 'STOP',   color: '#FF5757' },
  ]

  return (
    <div style={{
      display:      'flex',
      borderRadius: 6,
      border:       '1px solid rgba(255,255,255,0.08)',
      overflow:     'hidden',
      flexShrink:   0,
    }}>
      {segments.map((seg, i) => {
        const active = status === seg.id
        return (
          <motion.button
            key={seg.id}
            onClick={() => onSet(seg.id)}
            whileTap={{ scale: 0.97 }}
            transition={{ type: 'spring', stiffness: 400, damping: 28 }}
            style={{
              fontFamily:    'var(--fmo)',
              fontSize:      10,
              fontWeight:    600,
              letterSpacing: '0.12em',
              padding:       '7px 16px',
              borderLeft:    i > 0 ? '1px solid rgba(255,255,255,0.08)' : 'none',
              background:    active ? `${seg.color}1A` : 'transparent',
              color:         active ? seg.color : 'rgba(255,255,255,0.28)',
              cursor:        'pointer',
              outline:       'none',
              userSelect:    'none',
              transition:    'background 0.15s, color 0.15s',
            }}
          >
            {seg.label}
          </motion.button>
        )
      })}
    </div>
  )
}

// ─── MockToggle ───────────────────────────────────────────────────────────────
function MockToggle({ enabled, onToggle }: { enabled: boolean; onToggle: () => void }) {
  return (
    <button
      onClick={onToggle}
      title={`Mock mode: ${enabled ? 'ON' : 'OFF'}`}
      style={{
        display:    'flex',
        alignItems: 'center',
        gap:        5,
        padding:    '3px 8px',
        borderRadius: 4,
        border:     `1px solid ${enabled ? 'rgba(245,166,35,.32)' : 'rgba(255,255,255,0.09)'}`,
        background: enabled ? 'rgba(245,166,35,.07)' : 'transparent',
        cursor:     'pointer',
        outline:    'none',
        transition: 'border-color 0.15s, background 0.15s',
      }}
    >
      <div style={{
        width:        24,
        height:       12,
        borderRadius: 6,
        background:   enabled ? 'rgba(245,166,35,.40)' : 'rgba(255,255,255,.15)',
        position:     'relative',
        transition:   'background 0.2s',
        flexShrink:   0,
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
            background:   enabled ? '#F5A623' : 'rgba(255,255,255,0.65)',
          }}
        />
      </div>
      <span style={{
        fontFamily:    'var(--fmo)',
        fontSize:      10,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        color:         enabled ? '#F5A623' : 'rgba(255,255,255,0.55)',
        lineHeight:    1,
      }}>
        mock
      </span>
    </button>
  )
}


// ─── Header ───────────────────────────────────────────────────────────────────
export function Header() {
  const {
    autopilotStatus,
    llmStats,
    imageCostToday,
    feeCostToday,
    systemStatus,
    setAutopilotStatus,
  } = useStore(
    useShallow((s) => ({
      autopilotStatus:       s.autopilotStatus,
      llmStats:              s.llmStats,
      imageCostToday:        s.imageCostToday,
      feeCostToday:          s.feeCostToday,
      systemStatus:          s.systemStatus,
      setAutopilotStatus:    s.setAutopilotStatus,
    }))
  )

  const mockEnabled = (systemStatus as { mock_mode?: boolean }).mock_mode ?? false
  const toggleMock  = () => {
    fetch('/api/system/mock', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ enabled: !mockEnabled }),
    }).catch(() => {})
  }

  // Fetch autopilot status al mount e ogni 15s come fallback al WS
  const autopilotPollRef = useRef<ReturnType<typeof setInterval>>(undefined)
  useEffect(() => {
    const fetchStatus = () => {
      fetch('/api/autopilot/status')
        .then((r) => r.ok ? r.json() : null)
        .then((data) => { if (data?.status) setAutopilotStatus(data.status, data.current_niche ?? null) })
        .catch(() => {})
    }
    fetchStatus()
    autopilotPollRef.current = setInterval(fetchStatus, 15_000)
    return () => clearInterval(autopilotPollRef.current)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Format value strings
  const fmtUsd = (v: number) => v < 0.005 ? '$0' : v < 0.01 ? '<$0.01' : `$${v.toFixed(2)}`

  return (
    <div style={{
      position:     'fixed',
      top:          12,
      left:         142,
      right:        12,
      height:       52,
      zIndex:       20,
      display:      'flex',
      alignItems:   'center',
      padding:      '0 16px',
      gap:          12,
      borderRadius: 12,
      background:   'rgba(255,255,255,0.07)',
      border:       '1px solid rgba(255,255,255,0.10)',
      boxShadow:    '0 4px 24px rgba(0,0,0,0.22), inset 0 1px 0 rgba(255,255,255,0.10)',
      backdropFilter: 'blur(40px)',
      WebkitBackdropFilter: 'blur(40px)',
    }}>

      {/* ── Spacer ── */}
      <div style={{ flex: 1 }} />

      {/* ── Center: autopilot segmented ── */}
      <AutopilotSegmented
        status={autopilotStatus}
        onSet={(s) => {
          const endpoint = s === 'running' ? '/api/autopilot/start'
                         : s === 'paused'  ? '/api/autopilot/pause'
                         : '/api/autopilot/stop'
          fetch(endpoint, { method: 'POST' })
            .then(r => r.ok ? setAutopilotStatus(s) : null)
            .catch(() => {})
        }}
      />

      {/* ── Spacer ── */}
      <div style={{ flex: 1 }} />

      {/* ── Right: costi plain text ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0 }}>
        {[
          { label: 'LLM', value: fmtUsd(llmStats.runCost) },
          { label: 'IMG', value: fmtUsd(imageCostToday) },
          { label: 'FEE', value: fmtUsd(feeCostToday) },
        ].map(({ label, value }) => (
          <div key={label} style={{ display: 'flex', alignItems: 'baseline', gap: 5 }}>
            <span style={{ fontFamily: 'var(--fmo)', fontSize: 10, letterSpacing: '0.12em', color: 'rgba(255,255,255,0.38)', textTransform: 'uppercase' }}>
              {label}:
            </span>
            <span style={{ fontFamily: 'var(--fmo)', fontSize: 12, fontWeight: 500, color: 'rgba(255,255,255,0.75)' }}>
              {value}
            </span>
          </div>
        ))}
      </div>

      {/* ── Mock toggle ── */}
      <MockToggle enabled={mockEnabled} onToggle={toggleMock} />

    </div>
  )
}
