/**
 * NicheTable — tabella niche_intelligence con filtri e sorting.
 *
 * FE-Blocco 4, Step 4.3
 *
 * Dati: GET /api/etsy/niches (JOIN market_signals)
 * Polling: ogni 60s (dati poco volatili)
 *
 * Colonne:
 *   niche · type · tier · score bar (entry_score) · trend · perf · confidence · CTR
 *
 * Sorting client-side: entry_score (default) | performance_score | avg_ctr
 *
 * States: skeleton → live → error → empty
 */

import { useState, useEffect, useMemo, useCallback } from 'react'
import { motion, AnimatePresence }                    from 'framer-motion'
import type { NicheItem }                             from '../../types'

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function fmtProductType(pt: string | null): string {
  if (!pt) return '—'
  const MAP: Record<string, string> = {
    digital_print: 'print', digital_art_png: 'art', svg_bundle: 'svg',
    bundle: 'bundle', pod_print: 'pod', pod_mug: 'mug', pod_tshirt: 'tee',
  }
  return MAP[pt] ?? pt.replace(/_/g, ' ')
}

function fmtScore(n: number | null): string {
  return n != null ? n.toFixed(2) : '—'
}

function fmtCtr(n: number | null): string {
  if (n == null || n <= 0) return '—'
  return `${(n * 100).toFixed(1)}%`
}

/** Derive ↗ ↘ → from performance_score */
function trendChar(item: NicheItem): { char: string; color: string } {
  const s = item.performance_score
  if (s >= 0.62) return { char: '↗', color: '#1BFF5E' }
  if (s <= 0.36) return { char: '↘', color: '#FF4444' }
  return { char: '→', color: '#F5A623' }
}

/** Badge for tier 1/2/null */
function tierBadge(tier: number | null): { label: string; bg: string; fg: string } | null {
  if (tier === 1) return { label: 'GOLD',   bg: 'rgba(245,166,35,0.14)',  fg: '#F5A623' }
  if (tier === 2) return { label: 'SILVER', bg: 'rgba(200,200,255,0.10)', fg: '#B8BCC8' }
  return null
}

/** Status badge: ACTIVE or ANALYZING derived from score + confidence */
function statusBadge(item: NicheItem): { label: string; bg: string; fg: string } {
  const active = item.performance_score >= 0.50 &&
    (item.confidence_level === 'high' || item.confidence_level === 'medium')
  return active
    ? { label: 'ACTIVE',    bg: 'rgba(27,255,94,0.12)',  fg: '#1BFF5E' }
    : { label: 'ANALYZING', bg: 'rgba(245,166,35,0.10)', fg: '#F5A623' }
}

/* ── Score mini-bar ───────────────────────────────────────────────────────── */
interface ScoreBarProps { value: number | null; color: string }

function ScoreBar({ value, color }: ScoreBarProps) {
  const pct = value != null ? Math.max(0, Math.min(1, value)) : 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      <div style={{
        position:     'relative',
        width:        48,
        height:       4,
        borderRadius: 2,
        background:   'rgba(255,255,255,0.07)',
        overflow:     'hidden',
        flexShrink:   0,
      }}>
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct * 100}%` }}
          transition={{ type: 'spring', stiffness: 90, damping: 18, delay: 0.1 }}
          style={{
            position:     'absolute',
            top: 0, left: 0, bottom: 0,
            borderRadius: 2,
            background:   value != null ? color : 'transparent',
            opacity:      0.80,
          }}
        />
      </div>
      <span className="mono-num" style={{
        fontSize: 10, color: value != null ? 'rgba(255,255,255,0.60)' : 'rgba(255,255,255,0.22)',
        minWidth: 26, letterSpacing: '0.01em',
      }}>
        {fmtScore(value)}
      </span>
    </div>
  )
}

/* ── Column config ────────────────────────────────────────────────────────── */

type SortKey = 'entry_score' | 'performance_score' | 'avg_ctr'

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'entry_score',       label: 'Entry score' },
  { value: 'performance_score', label: 'Perf score'  },
  { value: 'avg_ctr',          label: 'CTR'          },
]

/* ── Grid template ────────────────────────────────────────────────────────── */
// niche(1fr) · tier(52px) · score(84px) · trend(22px) · status(72px)
const GRID = '1fr 52px 84px 22px 72px'

/* ── Skeleton row ─────────────────────────────────────────────────────────── */
function SkeletonRow({ delay = 0 }: { delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay, duration: 0.25 }}
      style={{
        display:             'grid',
        gridTemplateColumns: GRID,
        gap:                 8,
        padding:             '7px 0',
        alignItems:          'center',
        borderBottom:        '1px solid rgba(255,255,255,0.05)',
      }}
    >
      <div style={{ height: 9, borderRadius: 3, background: 'rgba(255,255,255,0.06)', maxWidth: 140 }} />
      <div style={{ height: 16, borderRadius: 3, background: 'rgba(255,255,255,0.06)' }} />
      <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
        <div style={{ height: 4, flex: 1, borderRadius: 2, background: 'rgba(255,255,255,0.05)' }} />
        <div style={{ height: 8, width: 22, borderRadius: 3, background: 'rgba(255,255,255,0.04)' }} />
      </div>
      <div style={{ height: 9, borderRadius: 3, background: 'rgba(255,255,255,0.04)' }} />
      <div style={{ height: 16, borderRadius: 3, background: 'rgba(255,255,255,0.06)' }} />
    </motion.div>
  )
}

/* ── Table row ────────────────────────────────────────────────────────────── */
interface NicheRowProps { item: NicheItem; index: number; isLast: boolean }

function NicheRow({ item, index, isLast }: NicheRowProps) {
  const trend  = trendChar(item)
  const tier   = tierBadge(item.tier)
  const status = statusBadge(item)

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, transition: { duration: 0.12 } }}
      transition={{ type: 'spring', stiffness: 340, damping: 30, delay: index * 0.035 }}
      style={{
        display:             'grid',
        gridTemplateColumns: GRID,
        gap:                 8,
        padding:             '6px 0',
        alignItems:          'center',
        borderBottom:        isLast ? 'none' : '1px solid rgba(255,255,255,0.05)',
        background:          index % 2 === 1 ? 'rgba(255,255,255,0.008)' : 'transparent',
      }}
    >
      {/* Niche */}
      <span className="mono-num" style={{
        fontSize: 11, color: 'rgba(255,255,255,0.78)',
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        letterSpacing: '0.01em',
      }}>
        {item.niche}
      </span>

      {/* Tier badge: GOLD / SILVER / — */}
      {tier ? (
        <span className="mono-num" style={{
          fontSize: 9, fontWeight: 600, letterSpacing: '0.06em',
          color: tier.fg, background: tier.bg,
          padding: '2px 4px', borderRadius: 3, textAlign: 'center',
          textTransform: 'uppercase',
        }}>
          {tier.label}
        </span>
      ) : (
        <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)' }}>—</span>
      )}

      {/* Entry score bar */}
      <ScoreBar value={item.entry_score} color="#F5A623" />

      {/* Trend arrow */}
      <span className="mono-num" style={{
        fontSize: 12, color: trend.color, textAlign: 'center', lineHeight: 1,
      }}>
        {trend.char}
      </span>

      {/* Status badge: ACTIVE / ANALYZING */}
      <span className="mono-num" style={{
        fontSize: 9, fontWeight: 600, letterSpacing: '0.06em',
        color: status.fg, background: status.bg,
        padding: '2px 5px', borderRadius: 3, textAlign: 'center',
        textTransform: 'uppercase',
      }}>
        {status.label}
      </span>
    </motion.div>
  )
}

/* ── NicheTable ───────────────────────────────────────────────────────────── */
export function NicheTable() {
  const [niches,  setNiches]  = useState<NicheItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const [nicheFilter, setNicheFilter] = useState('')
  const [sortKey,     setSortKey]     = useState<SortKey>('entry_score')

  const fetchNiches = useCallback(async () => {
    try {
      const res = await fetch('/api/etsy/niches')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { niches: NicheItem[] }
      setNiches(data.niches ?? [])
      setError(null)
    } catch {
      setError('Connessione fallita. Riprova.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchNiches()
    const id = setInterval(() => { void fetchNiches() }, 60_000)
    return () => clearInterval(id)
  }, [fetchNiches])

  /* ── Filtered + sorted rows ── */
  const rows = useMemo(() => {
    let list = niches
    if (nicheFilter.trim()) {
      const q = nicheFilter.trim().toLowerCase()
      list = list.filter(n => n.niche.toLowerCase().includes(q))
    }
    return [...list].sort((a, b) => {
      const va = sortKey === 'avg_ctr'
        ? (a.avg_ctr ?? -1)
        : sortKey === 'performance_score'
          ? a.performance_score
          : (a.entry_score ?? a.performance_score)
      const vb = sortKey === 'avg_ctr'
        ? (b.avg_ctr ?? -1)
        : sortKey === 'performance_score'
          ? b.performance_score
          : (b.entry_score ?? b.performance_score)
      return vb - va
    })
  }, [niches, nicheFilter, sortKey])

  /* ── Input style ── */
  const inputStyle: React.CSSProperties = {
    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 4, padding: '4px 8px',
    fontFamily: 'var(--fmo)', fontSize: 11,
    color: 'rgba(255,255,255,0.65)', letterSpacing: '0.02em',
    outline: 'none',
    transition: 'border-color 0.18s cubic-bezier(0.32, 0.72, 0, 1)',
  }

  return (
    <div style={{
      background:     'rgba(13,15,18,0.72)',
      border:         '1px solid rgba(255,255,255,0.07)',
      borderRadius:   10,
      padding:        '16px 18px 14px',
      backdropFilter: 'blur(12px)',
      boxShadow:      'inset 0 1px 0 rgba(255,255,255,0.07)',
    }}>
      {/* ── Section header ─────────────────────────────────────────────── */}
      <div className="hud-label" style={{ marginBottom: 14 }}>
        [ NICHES ]
      </div>

      {/* ── Error ──────────────────────────────────────────────────────── */}
      {error && (
        <div style={{
          border: '1px solid rgba(255,68,68,0.28)', borderRadius: 5,
          padding: '6px 10px', marginBottom: 12,
          fontFamily: 'var(--fmo)', fontSize: 11, color: '#FF6B6B', letterSpacing: '0.02em',
        }}>
          {error}
        </div>
      )}

      {/* ── Filters row ────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        {/* Sort dropdown */}
        <select
          value={sortKey}
          onChange={e => setSortKey(e.target.value as SortKey)}
          style={{ ...inputStyle, width: 128, cursor: 'pointer' }}
        >
          {SORT_OPTIONS.map(o => (
            <option key={o.value} value={o.value}
              style={{ background: '#0D0F12', color: 'rgba(255,255,255,0.75)' }}
            >
              {o.label}
            </option>
          ))}
        </select>

        {/* Niche filter */}
        <input
          type="text"
          placeholder="niche..."
          value={nicheFilter}
          onChange={e => setNicheFilter(e.target.value)}
          onFocus={e => (e.target.style.borderColor = 'rgba(245,166,35,0.40)')}
          onBlur={e  => (e.target.style.borderColor = 'rgba(255,255,255,0.08)')}
          style={{ ...inputStyle, flex: 1 }}
        />

        {/* Count */}
        {!loading && (
          <span className="mono-num" style={{
            fontSize: 10, color: 'rgba(255,255,255,0.28)',
            letterSpacing: '0.04em', whiteSpace: 'nowrap', flexShrink: 0,
          }}>
            {rows.length} / {niches.length}
          </span>
        )}
      </div>

      {/* ── Table header ───────────────────────────────────────────────── */}
      <div style={{
        display: 'grid', gridTemplateColumns: GRID, gap: 8,
        paddingBottom: 5, borderBottom: '1px solid rgba(255,255,255,0.09)',
        marginBottom: 2,
      }}>
        {[
          { key: 'niche',       label: 'NAME'   },
          { key: 'tier',        label: 'TIER'   },
          { key: 'entry_score', label: 'SCORE'  },
          { key: 'trend',       label: '↕'      },
          { key: 'status',      label: 'STATUS' },
        ].map(h => (
          <span
            key={h.key}
            className="hud-label"
            onClick={() => {
              if (h.key === 'entry_score' || h.key === 'performance_score' || h.key === 'avg_ctr') {
                setSortKey(h.key as SortKey)
              }
            }}
            style={{
              fontSize: 9, letterSpacing: '0.12em',
              cursor: ['entry_score','performance_score','avg_ctr'].includes(h.key) ? 'pointer' : 'default',
              color: sortKey === h.key ? 'rgba(245,166,35,0.80)' : undefined,
              userSelect: 'none',
            }}
          >
            {h.label}
          </span>
        ))}
      </div>

      {/* ── Table body ─────────────────────────────────────────────────── */}
      {loading ? (
        <div>
          {[0, 0.04, 0.08, 0.12, 0.16, 0.20].map((d, i) => (
            <SkeletonRow key={i} delay={d} />
          ))}
        </div>

      ) : rows.length === 0 ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.35 }}
          style={{ padding: '20px 0', textAlign: 'center' }}
        >
          <motion.div
            animate={{ y: [-2, 2, -2] }}
            transition={{ repeat: Infinity, duration: 3.5, ease: 'easeInOut' }}
            style={{ fontSize: 18, marginBottom: 8, opacity: 0.22 }}
          >
            ◈
          </motion.div>
          <span className="mono-num" style={{
            fontSize: 11, color: 'rgba(255,255,255,0.22)', letterSpacing: '0.04em',
          }}>
            {nicheFilter
              ? '— nessuna niche corrisponde al filtro —'
              : '— nessun dato niche_intelligence —'}
          </span>
        </motion.div>

      ) : (
        <AnimatePresence initial={false} mode="popLayout">
          {rows.map((item, i) => (
            <NicheRow
              key={`${item.niche}__${item.product_type ?? ''}`}
              item={item}
              index={i}
              isLast={i === rows.length - 1}
            />
          ))}
        </AnimatePresence>
      )}
    </div>
  )
}
