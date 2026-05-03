/**
 * CollectionStats — HUD panel (top-right, w=220px)
 *
 * Conteggi ChromaDB per collection + voice state label animato.
 *
 * Layout:
 *   pepe_memory      ● 1,247
 *   personal_memory  ● 432
 *   screen_memory    ● 891
 *   shared_memory    ●  56
 *   ─────────────────────
 *   ● Thinking…
 */

import { motion }        from 'framer-motion'
import { useStore }      from '../../store'
import { useUiStore }    from '../../store/uiStore'
import { HudPanel }      from '../ui/HudPanel'

const COLLECTIONS = ['pepe_memory', 'personal_memory', 'screen_memory', 'shared_memory'] as const

const COLL_COLOR: Record<string, string> = {
  pepe_memory:     '#F59E0B',
  personal_memory: '#4ADE80',
  screen_memory:   '#8B7CF6',
  shared_memory:   '#94A3B8',
}

const STATE_LABEL: Record<string, string> = {
  wakeword:  'Idle',
  listening: 'Listening…',
  thinking:  'Thinking…',
  speaking:  'Speaking…',
}

const STATE_COLOR: Record<string, string> = {
  wakeword:  '#6B7280',
  listening: '#4ADE80',
  thinking:  '#8B7CF6',
  speaking:  '#F59E0B',
}

function collLabel(c: string) { return c.replace(/_memory$/, '') }
function fmtCount(n: number)  { return n.toLocaleString('en-US') }

/* ── Skeleton row ─────────────────────────────────────────────────────────── */
function SkeletonRow({ delay = 0 }: { delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }}
      transition={{ delay, duration: 0.3 }}
      style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
               padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}
    >
      <div style={{ height: 9, width: 90, borderRadius: 3, background: 'rgba(255,255,255,0.06)' }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'rgba(255,255,255,0.08)' }} />
        <div style={{ height: 9, width: 34, borderRadius: 3, background: 'rgba(255,255,255,0.05)' }} />
      </div>
    </motion.div>
  )
}

/* ── Collection row ──────────────────────────────────────────────────────── */
function CollRow({ name, count, isLast }: { name: string; count: number | undefined; isLast: boolean }) {
  const color = COLL_COLOR[name] ?? '#6B7280'
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '4px 0', borderBottom: isLast ? 'none' : '1px solid rgba(255,255,255,0.05)' }}>
      <span className="mono-num" style={{ fontSize: 11, color: 'rgba(255,255,255,0.55)', letterSpacing: '0.02em' }}>
        {collLabel(name)}
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: color,
                      boxShadow: `0 0 5px ${color}55`, flexShrink: 0 }} />
        <span className="mono-num" style={{
          fontSize: 11, minWidth: 34, textAlign: 'right', letterSpacing: '0.02em',
          color: count !== undefined ? 'rgba(255,255,255,0.80)' : 'rgba(255,255,255,0.25)',
        }}>
          {count !== undefined ? fmtCount(count) : '—'}
        </span>
      </div>
    </div>
  )
}

/* ── CollectionStats ─────────────────────────────────────────────────────── */
export function CollectionStats() {
  const chromaStats = useStore(s => s.chromaStats)
  const orbState    = useUiStore(s => s.orbState)

  const byCollection = chromaStats?.by_collection
  const dotColor     = STATE_COLOR[orbState] ?? '#6B7280'
  const stateLabel   = STATE_LABEL[orbState] ?? orbState

  return (
    <HudPanel title="COLLECTIONS" style={{ top: 16, right: 16, width: 220, zIndex: 10 }}>
      {chromaStats === null ? (
        <div>
          <SkeletonRow delay={0}    />
          <SkeletonRow delay={0.06} />
          <SkeletonRow delay={0.12} />
          <SkeletonRow delay={0.18} />
        </div>
      ) : (
        <div>
          {COLLECTIONS.map((name, i) => (
            <CollRow key={name} name={name} count={byCollection?.[name]}
                     isLast={i === COLLECTIONS.length - 1} />
          ))}
        </div>
      )}

      <div style={{ height: 1, background: 'rgba(255,255,255,0.07)', margin: '8px 0 7px' }} />

      {/* Voice state row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <motion.div
          animate={{ opacity: [0.4, 1, 0.4] }}
          transition={{ repeat: Infinity, duration: 2, ease: 'easeInOut' }}
          style={{ width: 6, height: 6, borderRadius: '50%', background: dotColor,
                   boxShadow: `0 0 6px ${dotColor}88`, flexShrink: 0 }}
        />
        <span className="mono-num" style={{ fontSize: 11, color: 'rgba(255,255,255,0.55)', letterSpacing: '0.03em' }}>
          {stateLabel}
        </span>
      </div>
    </HudPanel>
  )
}
