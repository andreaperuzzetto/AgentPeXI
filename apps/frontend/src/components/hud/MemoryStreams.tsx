/**
 * MemoryStreams — HUD panel (top-left, w=280px)
 *
 * Feed live degli ultimi 8 eventi `memory_query`.
 * Dati: store.memoryQueryFeed — ring-buffer di 20 eventi.
 *
 * Layout row:
 *   ● AgentName     collection    12:34:01
 */

import { useMemo }                 from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useStore }                from '../../store'
import { HudPanel }                from '../ui/HudPanel'

const COLL_COLOR: Record<string, string> = {
  pepe_memory:     '#F59E0B',
  personal_memory: '#4ADE80',
  screen_memory:   '#8B7CF6',
  shared_memory:   '#94A3B8',
}

function collLabel(c: string) { return c.replace(/_memory$/, '') }

function fmtTime(ts: number) {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`
}

function fmtAgent(agent: string) {
  return agent.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join('')
}

/* ── Skeleton row ─────────────────────────────────────────────────────────── */
function SkeletonRow({ delay = 0 }: { delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }}
      transition={{ delay, duration: 0.3 }}
      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0',
               borderBottom: '1px solid rgba(255,255,255,0.05)' }}
    >
      <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'rgba(255,255,255,0.08)', flexShrink: 0 }} />
      <div style={{ height: 9, width: 80, borderRadius: 3, background: 'rgba(255,255,255,0.07)', flexShrink: 0 }} />
      <div style={{ height: 9, flex: 1, borderRadius: 3, background: 'rgba(255,255,255,0.04)' }} />
      <div style={{ height: 9, width: 44, borderRadius: 3, background: 'rgba(255,255,255,0.04)', flexShrink: 0 }} />
    </motion.div>
  )
}

/* ── Live event row ──────────────────────────────────────────────────────── */
function EventRow({ agent, collection, ts, isLast }: {
  agent: string; collection: string; ts: number; isLast: boolean
}) {
  const color = COLL_COLOR[collection] ?? '#6B7280'
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4, transition: { duration: 0.15 } }}
      transition={{ type: 'spring', stiffness: 380, damping: 32 }}
      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
               borderBottom: isLast ? 'none' : '1px solid rgba(255,255,255,0.05)' }}
    >
      <div style={{ width: 6, height: 6, borderRadius: '50%', background: color,
                    flexShrink: 0, boxShadow: `0 0 6px ${color}55` }} />
      <span className="mono-num" style={{
        fontSize: 11, color: 'rgba(255,255,255,0.75)', flexShrink: 0,
        width: 88, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        letterSpacing: '0.02em',
      }}>
        {fmtAgent(agent)}
      </span>
      <span className="mono-num" style={{
        fontSize: 10, color, flex: 1,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        opacity: 0.75, letterSpacing: '0.02em',
      }}>
        {collLabel(collection)}
      </span>
      <span className="mono-num" style={{
        fontSize: 10, color: 'rgba(255,255,255,0.30)', flexShrink: 0, letterSpacing: '0.01em',
      }}>
        {fmtTime(ts)}
      </span>
    </motion.div>
  )
}

/* ── MemoryStreams ────────────────────────────────────────────────────────── */
export function MemoryStreams() {
  const wsConnected = useStore(s => s.wsConnected)
  const rawFeed     = useStore(s => s.memoryQueryFeed)

  const events = useMemo(() => [...rawFeed].reverse().slice(0, 8), [rawFeed])

  return (
    <HudPanel title="MEMORY STREAMS" style={{ top: 16, left: 16, width: 280, zIndex: 10 }}>
      {!wsConnected ? (
        <div>
          <SkeletonRow delay={0}    />
          <SkeletonRow delay={0.06} />
          <SkeletonRow delay={0.12} />
        </div>
      ) : events.length === 0 ? (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4 }}
          className="mono-num"
          style={{ fontSize: 11, color: 'rgba(255,255,255,0.22)', padding: '8px 0',
                   textAlign: 'center', letterSpacing: '0.04em' }}>
          — nessun evento recente —
        </motion.div>
      ) : (
        <AnimatePresence initial={false} mode="popLayout">
          {events.map((ev, i) => (
            <EventRow key={`${ev.agent}-${ev.ts}`}
              agent={ev.agent} collection={ev.collection} ts={ev.ts}
              isLast={i === events.length - 1} />
          ))}
        </AnimatePresence>
      )}
    </HudPanel>
  )
}
