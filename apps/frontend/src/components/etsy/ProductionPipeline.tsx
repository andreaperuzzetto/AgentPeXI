/**
 * ProductionPipeline — pannello EtsyView (larghezza piena)
 *
 * Pipeline bar segmentata proporzionale ai 5 status attivi
 * + tabella items recenti con filtri status/niche.
 *
 * FE-Blocco 4, Step 4.1
 *
 * Layout:
 *   [ PIPELINE ]
 *   [━━━━━━━━━━━━━━━━━━━━ bar ━━━━━━━━━━━━━━━━━━━━]
 *   pending_design 4   pending_approval 2 ...
 *
 *   [Status: all ▾]  [Niche: _____]      refresh
 *
 *   ID   Niche              Type           Status          Score  Price  Date
 *   1    wedding planner    digital_print  PENDING DESIGN  0.84   —      27 apr
 *   …
 *
 * Data: GET /api/production-queue?limit=200
 * Polling: ogni 20s
 *
 * States: loading (skeleton) → live → error
 */

import { useState, useEffect, useMemo, useCallback } from 'react'
import { motion, AnimatePresence }                    from 'framer-motion'
import { PipelineBar }                                from '../ui/PipelineBar'
import type { ProductionQueueItem }                   from '../../types'

/* ── Constants ─────────────────────────────────────────────────────────────── */

/* 5 status attivi per la pipeline bar — in ordine di pipeline */
const PIPELINE_STAGES = [
  { key: 'pending_design',   label: 'Design',    color: '#F5A623' },
  { key: 'pending_approval', label: 'Approval',  color: '#C8C8FF' },
  { key: 'approved',         label: 'Approved',  color: '#B57BFF' },
  { key: 'scheduled',        label: 'Scheduled', color: 'rgba(27,255,94,0.75)' },
  { key: 'published',        label: 'Published', color: '#1BFF5E' },
] as const

/* Status badge metadata — tutti i valori possibili inclusi quelli legacy */
interface StatusMeta { label: string; bg: string; fg: string }

const STATUS_META: Record<string, StatusMeta> = {
  pending_design:   { label: 'Design',    bg: 'rgba(245,166,35,0.14)',   fg: '#F5A623' },
  pending_approval: { label: 'Approval',  bg: 'rgba(200,200,255,0.14)',  fg: '#C8C8FF' },
  approved:         { label: 'Approved',  bg: 'rgba(181,123,255,0.14)',  fg: '#B57BFF' },
  scheduled:        { label: 'Scheduled', bg: 'rgba(27,255,94,0.10)',    fg: 'rgba(27,255,94,0.80)' },
  published:        { label: 'Published', bg: 'rgba(27,255,94,0.17)',    fg: '#1BFF5E' },
  skipped:          { label: 'Skipped',   bg: 'rgba(139,141,152,0.14)',  fg: '#8B8D98' },
  discarded:        { label: 'Discarded', bg: 'rgba(139,141,152,0.10)',  fg: 'rgba(139,141,152,0.65)' },
  failed:           { label: 'Failed',    bg: 'rgba(255,68,68,0.14)',    fg: '#FF4444' },
  /* legacy */
  planned:          { label: 'Planned',   bg: 'rgba(245,166,35,0.14)',   fg: '#F5A623' },
  in_progress:      { label: 'Running',   bg: 'rgba(181,123,255,0.14)',  fg: '#B57BFF' },
  completed:        { label: 'Done',      bg: 'rgba(27,255,94,0.17)',    fg: '#1BFF5E' },
}

function statusMeta(s: string): StatusMeta {
  return STATUS_META[s] ?? { label: s.toUpperCase().slice(0, 8), bg: 'rgba(139,141,152,0.10)', fg: '#8B8D98' }
}

/* Product type: short label */
function fmtProductType(pt: string): string {
  const MAP: Record<string, string> = {
    digital_print: 'print',
    digital_art_png: 'art png',
    svg_bundle: 'svg',
    bundle: 'bundle',
    pod_print: 'pod print',
    pod_mug: 'pod mug',
    pod_tshirt: 'pod tee',
  }
  return MAP[pt] ?? pt.replace(/_/g, ' ')
}

/* Date: "27 apr" */
const MONTHS = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']
function fmtDate(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`
}

/* Relative time: "2d ago", "3mo ago" */
function fmtRelTime(iso: string): string {
  if (!iso) return '—'
  const diffMs   = Date.now() - new Date(iso).getTime()
  const diffDays = Math.floor(diffMs / 86_400_000)
  if (diffDays < 1)   return 'today'
  if (diffDays < 7)   return `${diffDays}d ago`
  if (diffDays < 31)  return `${Math.floor(diffDays / 7)}w ago`
  if (diffDays < 365) return `${Math.floor(diffDays / 30)}mo ago`
  return `${Math.floor(diffDays / 365)}y ago`
}

/* Price */
function fmtPrice(p: number | null | undefined): string {
  if (p == null || p <= 0) return '—'
  return `€${p.toFixed(2)}`
}

/* Score */
function fmtScore(s: number | null | undefined): string {
  if (s == null) return '—'
  return s.toFixed(2)
}

/* Filter status options for the dropdown */
const STATUS_OPTIONS = [
  { value: 'all',              label: 'All statuses' },
  { value: 'pending_design',   label: 'Design' },
  { value: 'pending_approval', label: 'Approval' },
  { value: 'approved',         label: 'Approved' },
  { value: 'scheduled',        label: 'Scheduled' },
  { value: 'published',        label: 'Published' },
  { value: 'skipped',          label: 'Skipped' },
  { value: 'failed',           label: 'Failed' },
  { value: 'discarded',        label: 'Discarded' },
]

/* ── Skeleton row ───────────────────────────────────────────────────────────── */
/* Grid: name(1fr) · status(92px) · type(68px) · lastDate(68px) · published(72px) */
const ROW_GRID = '1fr 92px 68px 68px 72px'

function SkeletonRow({ delay = 0 }: { delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay, duration: 0.25 }}
      style={{
        display:      'grid',
        gridTemplateColumns: ROW_GRID,
        gap:          8,
        padding:      '7px 0',
        alignItems:   'center',
        borderBottom: '1px solid rgba(255,255,255,0.05)',
      }}
    >
      <div style={{ height: 9, borderRadius: 3, background: 'rgba(255,255,255,0.05)', maxWidth: 160 }} />
      <div style={{ height: 16, borderRadius: 3, background: 'rgba(255,255,255,0.06)' }} />
      <div style={{ height: 9, borderRadius: 3, background: 'rgba(255,255,255,0.04)' }} />
      <div style={{ height: 9, borderRadius: 3, background: 'rgba(255,255,255,0.04)' }} />
      <div style={{ height: 9, borderRadius: 3, background: 'rgba(255,255,255,0.03)' }} />
    </motion.div>
  )
}

/* ── Skeleton bar ───────────────────────────────────────────────────────────── */
function SkeletonBar() {
  return (
    <div>
      <div style={{
        height: 8, borderRadius: 4,
        background: 'rgba(255,255,255,0.05)',
        animation: 'shimmer 2.2s ease-in-out infinite',
        backgroundSize: '300% 100%',
      }} />
      <div style={{ display: 'flex', gap: 24, marginTop: 8 }}>
        {[80, 60, 50, 70, 40].map((w, i) => (
          <div key={i} style={{
            display: 'flex', flexDirection: 'column', gap: 3,
          }}>
            <div style={{ height: 7, width: w * 0.6, borderRadius: 3, background: 'rgba(255,255,255,0.05)' }} />
            <div style={{ height: 9, width: w * 0.4, borderRadius: 3, background: 'rgba(255,255,255,0.07)' }} />
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── Table row ─────────────────────────────────────────────────────────────── */
interface RowProps {
  item:   ProductionQueueItem
  index:  number
  isLast: boolean
}

function TableRow({ item, index, isLast }: RowProps) {
  const meta       = statusMeta(item.status)
  const isPublished = item.status === 'published' || item.status === 'completed'

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, transition: { duration: 0.12 } }}
      transition={{ type: 'spring', stiffness: 340, damping: 30, delay: index * 0.04 }}
      style={{
        display:             'grid',
        gridTemplateColumns: ROW_GRID,
        gap:                 8,
        padding:             '6px 0',
        alignItems:          'center',
        borderBottom:        isLast ? 'none' : '1px solid rgba(255,255,255,0.05)',
        background:          index % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.008)',
      }}
    >
      {/* Name (niche) */}
      <span className="mono-num" style={{
        fontSize: 11, color: 'rgba(255,255,255,0.75)',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        letterSpacing: '0.01em',
      }}>
        {item.niche}
      </span>

      {/* Status badge */}
      <span className="mono-num" style={{
        display:       'inline-block',
        fontSize:      9,
        fontWeight:    600,
        letterSpacing: '0.07em',
        textTransform: 'uppercase',
        color:         meta.fg,
        background:    meta.bg,
        padding:       '2px 6px',
        borderRadius:  3,
        whiteSpace:    'nowrap',
        overflow:      'hidden',
        textOverflow:  'ellipsis',
      }}>
        {meta.label}
      </span>

      {/* Type / Company */}
      <span className="mono-num" style={{
        fontSize: 10, color: 'rgba(255,255,255,0.38)', letterSpacing: '0.02em',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {fmtProductType(item.product_type)}
      </span>

      {/* Last Date */}
      <span className="mono-num" style={{
        fontSize: 10, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.01em',
      }}>
        {fmtDate(item.updated_at)}
      </span>

      {/* Published (relative time, only when published) */}
      <span className="mono-num" style={{
        fontSize: 10,
        color: isPublished ? 'rgba(27,255,94,0.70)' : 'rgba(255,255,255,0.20)',
        letterSpacing: '0.01em',
      }}>
        {isPublished ? fmtRelTime(item.updated_at) : '—'}
      </span>
    </motion.div>
  )
}
/* ── ProductionPipeline ────────────────────────────────────────────────────── */
export function ProductionPipeline() {
  const [items,   setItems]   = useState<ProductionQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  /* Filters */
  const [statusFilter, setStatusFilter] = useState('all')
  const [nicheFilter,  setNicheFilter]  = useState('')

  /* Fetch */
  const fetchItems = useCallback(async () => {
    try {
      const res = await fetch('/api/production-queue?limit=200')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { items: ProductionQueueItem[] }
      setItems(data.items ?? [])
      setError(null)
    } catch (e) {
      setError('Connessione fallita. Riprova.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchItems()
    const id = setInterval(() => { void fetchItems() }, 20_000)
    return () => clearInterval(id)
  }, [fetchItems])

  /* ── Pipeline bar segments ── */
  const barSegments = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const item of items) counts[item.status] = (counts[item.status] ?? 0) + 1

    /* Normalizza legacy status → pipeline stage */
    const norm = {
      ...counts,
      pending_design:   (counts.pending_design ?? 0) + (counts.planned ?? 0) + (counts.in_progress ?? 0),
      published:        (counts.published ?? 0) + (counts.completed ?? 0),
    }

    return PIPELINE_STAGES.map(s => ({ ...s, count: norm[s.key] ?? 0 }))
  }, [items])

  /* ── Filtered table items (max 20) ── */
  const tableItems = useMemo(() => {
    let list = items
    if (statusFilter !== 'all') list = list.filter(i => i.status === statusFilter)
    if (nicheFilter.trim())     list = list.filter(i => i.niche.toLowerCase().includes(nicheFilter.trim().toLowerCase()))
    return list.slice(0, 20)
  }, [items, statusFilter, nicheFilter])

  /* ── Input style ── */
  const inputStyle: React.CSSProperties = {
    background:    'rgba(255,255,255,0.04)',
    border:        '1px solid rgba(255,255,255,0.08)',
    borderRadius:  4,
    padding:       '4px 8px',
    fontFamily:    'var(--fmo)',
    fontSize:      11,
    color:         'rgba(255,255,255,0.65)',
    letterSpacing: '0.02em',
    outline:       'none',
    transition:    'border-color 0.18s cubic-bezier(0.32, 0.72, 0, 1)',
    width:         '100%',
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 280, damping: 30 }}
      style={{
        background:   'rgba(13,15,18,0.72)',
        border:       '1px solid rgba(255,255,255,0.07)',
        borderRadius: 10,
        padding:      '16px 18px 14px',
        backdropFilter: 'blur(12px)',
        boxShadow:    'inset 0 1px 0 rgba(255,255,255,0.07)',
      }}
    >
      {/* ── Section header ─────────────────────────────────────────────── */}
      <div className="hud-label" style={{ marginBottom: 14 }}>
        [ PIPELINE ]
      </div>

      {/* ── Error state ────────────────────────────────────────────────── */}
      {error && (
        <div style={{
          border:       '1px solid rgba(255,68,68,0.28)',
          borderRadius: 5,
          padding:      '6px 10px',
          marginBottom: 12,
          fontFamily:   'var(--fmo)',
          fontSize:     11,
          color:        '#FF6B6B',
          letterSpacing:'0.02em',
        }}>
          {error}
        </div>
      )}

      {/* ── Pipeline bar ───────────────────────────────────────────────── */}
      <div style={{ marginBottom: 18 }}>
        {loading ? <SkeletonBar /> : <PipelineBar segments={barSegments} />}
      </div>

      {/* ── Filters row ────────────────────────────────────────────────── */}
      <div style={{
        display:       'flex',
        gap:           8,
        marginBottom:  12,
        alignItems:    'center',
      }}>
        {/* Status dropdown */}
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          style={{ ...inputStyle, width: 140, cursor: 'pointer' }}
        >
          {STATUS_OPTIONS.map(o => (
            <option key={o.value} value={o.value}
              style={{ background: '#0D0F12', color: 'rgba(255,255,255,0.75)' }}
            >
              {o.label}
            </option>
          ))}
        </select>

        {/* Niche text filter */}
        <input
          type="text"
          placeholder="niche..."
          value={nicheFilter}
          onChange={e => setNicheFilter(e.target.value)}
          onFocus={e => (e.target.style.borderColor = 'rgba(245,166,35,0.40)')}
          onBlur={e  => (e.target.style.borderColor = 'rgba(255,255,255,0.08)')}
          style={{ ...inputStyle, flex: 1 }}
        />

        {/* Total count label */}
        {!loading && (
          <span className="mono-num" style={{
            fontSize: 10, color: 'rgba(255,255,255,0.28)',
            letterSpacing: '0.04em', whiteSpace: 'nowrap', flexShrink: 0,
          }}>
            {tableItems.length} / {items.length}
          </span>
        )}
      </div>

      {/* ── Table header ───────────────────────────────────────────────── */}
      <div style={{
        display:             'grid',
        gridTemplateColumns: ROW_GRID,
        gap:                 8,
        paddingBottom:       5,
        borderBottom:        '1px solid rgba(255,255,255,0.09)',
        marginBottom:        2,
      }}>
        {['NAME', 'STATUS', 'TYPE', 'LAST DATE', 'PUBLISHED'].map(h => (
          <span key={h} className="hud-label" style={{ fontSize: 9, letterSpacing: '0.12em' }}>
            {h}
          </span>
        ))}
      </div>

      {/* ── Table body ─────────────────────────────────────────────────── */}
      {loading ? (
        /* Skeleton rows */
        <div>
          {[0, 0.05, 0.10, 0.15, 0.20, 0.25].map((d, i) => (
            <SkeletonRow key={i} delay={d} />
          ))}
        </div>

      ) : tableItems.length === 0 ? (
        /* Empty state */
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.35 }}
          style={{
            padding:       '20px 0',
            textAlign:     'center',
          }}
        >
          <motion.div
            animate={{ y: [-2, 2, -2] }}
            transition={{ repeat: Infinity, duration: 3.5, ease: 'easeInOut' }}
            style={{ fontSize: 20, marginBottom: 8, opacity: 0.25 }}
          >
            ▣
          </motion.div>
          <span className="mono-num" style={{
            fontSize: 11, color: 'rgba(255,255,255,0.22)', letterSpacing: '0.04em',
          }}>
            {nicheFilter || statusFilter !== 'all'
              ? '— nessun item corrisponde al filtro —'
              : '— pipeline vuota —'}
          </span>
        </motion.div>

      ) : (
        /* Live rows */
        <AnimatePresence initial={false} mode="popLayout">
          {tableItems.map((item, i) => (
            <TableRow
              key={item.id}
              item={item}
              index={i}
              isLast={i === tableItems.length - 1}
            />
          ))}
        </AnimatePresence>
      )}
    </motion.div>
  )
}
