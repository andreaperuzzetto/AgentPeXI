/**
 * BridgeActivity — HUD panel (bottom-left, w=320px)
 *
 * Ultimi 4 eventi KnowledgeBridge ricevuti via WS `knowledge_bridge`.
 * Dati: store.bridgeFeed
 */

import { useMemo }                 from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useStore }                from '../../store'
import { HudPanel }                from '../ui/HudPanel'

const BRIDGE_COLOR   = '#94A3B8'
const ETSY_COLOR     = '#F59E0B'
const PERSONAL_COLOR = '#4ADE80'
const MONTHS = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']

function fmtBridgeTime(ts: number): string {
  const d = new Date(ts)
  return `${MONTHS[d.getMonth()]} ${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`
}

function truncSrc(s: string, max = 28): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

/* ── Skeleton event ──────────────────────────────────────────────────────── */
function SkeletonEvent({ delay = 0 }: { delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }}
      transition={{ delay, duration: 0.3 }}
      style={{ display: 'flex', flexDirection: 'column', gap: 5, padding: '7px 0',
               borderBottom: '1px solid rgba(255,255,255,0.05)' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ height: 9, width: 100, borderRadius: 3, background: 'rgba(255,255,255,0.08)' }} />
        <div style={{ height: 9, width: 56, borderRadius: 3, background: 'rgba(255,255,255,0.04)', flexShrink: 0 }} />
      </div>
      <div style={{ height: 9, width: '85%', borderRadius: 3, background: 'rgba(255,255,255,0.05)' }} />
    </motion.div>
  )
}

/* ── Bridge event row ────────────────────────────────────────────────────── */
function BridgeRow({ topic, source_etsy, source_personal, ts, isLast }: {
  topic: string; source_etsy: string; source_personal: string; ts: number; isLast: boolean
}) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4, transition: { duration: 0.15 } }}
      transition={{ type: 'spring', stiffness: 380, damping: 32 }}
      style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '7px 0',
               borderBottom: isLast ? 'none' : '1px solid rgba(255,255,255,0.05)' }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
        <span className="mono-num" style={{
          fontSize: 11, color: BRIDGE_COLOR, letterSpacing: '0.02em',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          [{topic}]
        </span>
        <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.28)', flexShrink: 0, letterSpacing: '0.01em' }}>
          {fmtBridgeTime(ts)}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, paddingLeft: 2 }}>
        <span className="mono-num" style={{
          fontSize: 10, color: ETSY_COLOR, opacity: 0.75,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 120,
          letterSpacing: '0.01em',
        }}>
          "{truncSrc(source_etsy, 22)}"
        </span>
        <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.30)', flexShrink: 0 }}>↔</span>
        <span className="mono-num" style={{
          fontSize: 10, color: PERSONAL_COLOR, opacity: 0.75,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 120,
          letterSpacing: '0.01em',
        }}>
          "{truncSrc(source_personal, 22)}"
        </span>
      </div>
    </motion.div>
  )
}

/* ── BridgeActivity ──────────────────────────────────────────────────────── */
export function BridgeActivity() {
  const wsConnected = useStore(s => s.wsConnected)
  const rawFeed     = useStore(s => s.bridgeFeed)

  const events = useMemo(() => [...rawFeed].reverse().slice(0, 4), [rawFeed])

  return (
    <HudPanel title="BRIDGE" style={{ bottom: 16, left: 16, width: 320, zIndex: 10 }}>
      {!wsConnected ? (
        <div>
          <SkeletonEvent delay={0}    />
          <SkeletonEvent delay={0.07} />
        </div>
      ) : events.length === 0 ? (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4 }}
          className="mono-num"
          style={{ fontSize: 11, color: 'rgba(255,255,255,0.22)', padding: '8px 0',
                   textAlign: 'center', letterSpacing: '0.04em' }}>
          — nessun bridge attivo —
        </motion.div>
      ) : (
        <AnimatePresence initial={false} mode="popLayout">
          {events.map((ev, i) => (
            <BridgeRow key={`${ev.topic}-${ev.ts}`}
              topic={ev.topic} source_etsy={ev.source_etsy}
              source_personal={ev.source_personal} ts={ev.ts}
              isLast={i === events.length - 1} />
          ))}
        </AnimatePresence>
      )}
    </HudPanel>
  )
}
