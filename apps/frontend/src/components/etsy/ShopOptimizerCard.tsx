/**
 * ShopOptimizerCard — stato ShopProfileOptimizer + preview inline.
 *
 * FE-Blocco 4, Step 4.6
 *
 * Dati: GET /api/etsy/shop-optimizer (no polling — dati stabili, reload on preview)
 * Action: POST /api/etsy/shop-optimizer/preview → panel inline
 *
 * Layout:
 *   [ SHOP OPTIMIZER ]               last: lunedì 07:00
 *     Titolo applicato:  "Premium Digital Printables · Wedding · …"
 *     Niches:            wedding planner, nursery decor, digital art
 *
 *     [ Preview  → ]
 *     ↳ (panel) nuovo titolo · about · niches · changed badge
 *
 * States:
 *   loading    → skeleton 2 righe
 *   never_applied → empty state flat
 *   applied    → dati + preview button
 *   previewing → button spinner
 *   preview_ok → panel AnimatePresence
 *   error      → inline error
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { motion, AnimatePresence }                   from 'framer-motion'

/* ── Types ────────────────────────────────────────────────────────────────── */

interface ShopOptimizerData {
  last_title:      string | null
  last_niches:     string[]
  last_applied_at: number | null
  status:          'applied' | 'never_applied'
}

interface PreviewResult {
  title:   string | null
  about:   string | null
  niches:  string[]
  changed: boolean
  status:  string
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function fmtAppliedAt(ts: number | null): string {
  if (!ts) return '—'
  const d    = new Date(ts * 1000)
  const now  = new Date()
  const diffH = (now.getTime() - d.getTime()) / 3_600_000

  if (diffH < 1) return `${Math.round(diffH * 60)}m fa`
  if (diffH < 24) {
    const hh = d.getHours().toString().padStart(2, '0')
    const mm = d.getMinutes().toString().padStart(2, '0')
    return `oggi ${hh}:${mm}`
  }
  const day = d.toLocaleDateString('it-IT', { weekday: 'long' })
  const hh  = d.getHours().toString().padStart(2, '0')
  const mm  = d.getMinutes().toString().padStart(2, '0')
  return `${day} ${hh}:${mm}`
}

/* ── Skeleton ─────────────────────────────────────────────────────────────── */

function SkeletonRow({ delay = 0, width = 220 }: { delay?: number; width?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay, duration: 0.28 }}
      style={{
        display:      'flex',
        alignItems:   'center',
        gap:          10,
        padding:      '7px 0',
        borderBottom: '1px solid rgba(255,255,255,0.05)',
      }}
    >
      <div style={{ height: 9,  width: 80,    borderRadius: 3, background: 'rgba(255,255,255,0.06)' }} />
      <div style={{ height: 11, width,         borderRadius: 3, background: 'rgba(255,255,255,0.09)' }} />
    </motion.div>
  )
}

/* ── Full-width CTA button ────────────────────────────────────────────────── */

interface PreviewButtonProps {
  loading:  boolean
  open:     boolean
  onClick:  () => void
}

function PreviewButton({ loading, open, onClick }: PreviewButtonProps) {
  return (
    <motion.button
      onClick={onClick}
      disabled={loading}
      whileHover={loading ? {} : { scale: 1.012 }}
      whileTap={loading   ? {} : { scale: 0.97, y: 1 }}
      transition={{ type: 'spring', stiffness: 400, damping: 20 }}
      style={{
        width:         '100%',
        display:       'flex',
        alignItems:    'center',
        justifyContent:'center',
        gap:           8,
        background:    open ? 'rgba(181,123,255,0.14)' : 'rgba(181,123,255,0.08)',
        border:        `1px solid ${open ? 'rgba(181,123,255,0.40)' : 'rgba(181,123,255,0.22)'}`,
        borderRadius:  6,
        padding:       '9px 14px',
        cursor:        loading ? 'default' : 'pointer',
        opacity:       loading ? 0.65 : 1,
      }}
    >
      <span className="mono-num" style={{
        fontSize:      10,
        letterSpacing: '0.10em',
        color:         open ? '#B57BFF' : 'rgba(181,123,255,0.80)',
        textTransform: 'uppercase',
        fontWeight:    600,
      }}>
        {loading ? 'Generating…' : 'PREVIEW OPTIMIZATIONS'}
      </span>
      <motion.span
        animate={{ rotate: loading ? 360 : 0, x: open ? 0 : [0, 3, 0] }}
        transition={loading
          ? { repeat: Infinity, duration: 1, ease: 'linear' }
          : open ? {} : { repeat: Infinity, duration: 2.5, ease: 'easeInOut' }
        }
        style={{ fontSize: 12, color: open ? '#B57BFF' : 'rgba(181,123,255,0.70)' }}
      >
        →
      </motion.span>
    </motion.button>
  )
}

/* ── Optimization score bar ──────────────────────────────────────────────── */

interface OptimizationScoreProps {
  data: { last_title: string | null; last_niches: string[]; last_applied_at: number | null }
}

function OptimizationScore({ data }: OptimizationScoreProps) {
  const score = useMemo(() => {
    let s = 0
    if (data.last_title)        s += 60
    if (data.last_niches.length) s += Math.min(data.last_niches.length, 8) * 12
    if (data.last_applied_at) {
      // eslint-disable-next-line react-hooks/purity
      const ageDays = (Date.now() / 1000 - data.last_applied_at) / 86_400
      if (ageDays < 7)  s += 44
      else if (ageDays < 30) s += 22
    }
    return Math.min(s, 200)
  }, [data])

  const pct   = score / 200
  const color = pct >= 0.75 ? '#1BFF5E' : pct >= 0.50 ? '#F5A623' : '#FF6B6B'

  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
        <span className="hud-label" style={{ fontSize: 8 }}>OPTIMIZATION SCORE</span>
        <span className="mono-num" style={{ fontSize: 13, color, fontWeight: 600 }}>
          {score}<span style={{ fontSize: 9, color: 'rgba(255,255,255,0.28)', marginLeft: 2 }}>/200</span>
        </span>
      </div>
      <div style={{
        height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.07)', overflow: 'hidden',
      }}>
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct * 100}%` }}
          transition={{ type: 'spring', stiffness: 80, damping: 18, delay: 0.2 }}
          style={{ height: '100%', borderRadius: 2, background: color, opacity: 0.80 }}
        />
      </div>
    </div>
  )
}

/* ── Preview panel ────────────────────────────────────────────────────────── */

interface PreviewPanelProps {
  result: PreviewResult
}

function PreviewPanel({ result }: PreviewPanelProps) {
  return (
    <motion.div
      key="preview-panel"
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: 'auto', opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ type: 'spring', stiffness: 260, damping: 28 }}
      style={{ overflow: 'hidden' }}
    >
      <div style={{
        background:    'rgba(181,123,255,0.04)',
        border:        '1px solid rgba(181,123,255,0.14)',
        borderRadius:  6,
        padding:       '10px 12px',
        marginTop:     8,
        display:       'flex',
        flexDirection: 'column',
        gap:           8,
      }}>
        {/* Changed badge */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div className="hud-label" style={{ fontSize: 9 }}>Preview generata</div>
          <span className="mono-num" style={{
            fontSize:   9,
            color:      result.changed ? '#1BFF5E' : 'rgba(255,255,255,0.35)',
            background: result.changed ? 'rgba(27,255,94,0.10)' : 'rgba(255,255,255,0.05)',
            padding:    '1px 5px',
            borderRadius: 3,
            letterSpacing: '0.04em',
          }}>
            {result.changed ? 'CHANGED' : 'UNCHANGED'}
          </span>
        </div>

        {/* New title */}
        {result.title && (
          <div>
            <div className="hud-label" style={{ fontSize: 9, marginBottom: 4 }}>
              Nuovo titolo
            </div>
            <span className="mono-num" style={{
              fontSize:    11,
              color:       'rgba(255,255,255,0.72)',
              lineHeight:  1.5,
              display:     'block',
            }}>
              "{result.title}"
            </span>
          </div>
        )}

        {/* Niches */}
        {result.niches.length > 0 && (
          <div>
            <div className="hud-label" style={{ fontSize: 9, marginBottom: 3 }}>
              Niches ({result.niches.length})
            </div>
            <span className="mono-num" style={{
              fontSize:  10,
              color:     'rgba(255,255,255,0.42)',
              lineHeight: 1.6,
            }}>
              {result.niches.join(', ')}
            </span>
          </div>
        )}

        {/* About excerpt — max 2 lines */}
        {result.about && (
          <div>
            <div className="hud-label" style={{ fontSize: 9, marginBottom: 3 }}>About</div>
            <span className="mono-num" style={{
              fontSize:        10,
              color:           'rgba(255,255,255,0.32)',
              lineHeight:      1.55,
              display:         '-webkit-box',
              WebkitLineClamp: 3,
              WebkitBoxOrient: 'vertical',
              overflow:        'hidden',
            }}>
              {result.about}
            </span>
          </div>
        )}
      </div>
    </motion.div>
  )
}

/* ── ShopOptimizerCard ────────────────────────────────────────────────────── */

export function ShopOptimizerCard() {
  const [data,        setData]        = useState<ShopOptimizerData | null>(null)
  const [loading,     setLoading]     = useState(true)
  const [error,       setError]       = useState<string | null>(null)
  const [previewing,  setPreviewing]  = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [preview,     setPreview]     = useState<PreviewResult | null>(null)
  const [previewErr,  setPreviewErr]  = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch('/api/etsy/shop-optimizer')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json() as ShopOptimizerData
      setData(json)
      setError(null)
    } catch {
      setError('Connessione fallita. Riprova.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void fetchData() }, [fetchData])

  const handlePreview = useCallback(async () => {
    if (previewOpen && preview) {
      /* Toggle off */
      setPreviewOpen(false)
      return
    }
    setPreviewing(true)
    setPreviewErr(null)
    try {
      const res = await fetch('/api/etsy/shop-optimizer/preview', { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json() as PreviewResult
      setPreview(json)
      setPreviewOpen(true)
    } catch {
      setPreviewErr('Preview fallita. Riprova.')
    } finally {
      setPreviewing(false)
    }
  }, [previewOpen, preview])

  return (
    <div style={{
      background:     'rgba(13,15,18,0.72)',
      border:         '1px solid rgba(255,255,255,0.07)',
      borderRadius:   10,
      padding:        '16px 18px 14px',
      backdropFilter: 'blur(12px)',
      boxShadow:      'inset 0 1px 0 rgba(255,255,255,0.07)',
    }}>
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
        <div className="hud-label" style={{ flex: 1 }}>
          [ SHOP OPTIMIZER ]
        </div>
        {!loading && data?.last_applied_at && (
          <span className="mono-num" style={{
            fontSize: 9, color: 'rgba(255,255,255,0.28)', letterSpacing: '0.04em',
          }}>
            last: {fmtAppliedAt(data.last_applied_at)}
          </span>
        )}
        {!loading && data?.status === 'applied' && (
          <span className="mono-num" style={{
            fontSize:   9,
            color:      '#B57BFF',
            background: 'rgba(181,123,255,0.10)',
            padding:    '2px 6px',
            borderRadius: 3,
            marginLeft: 8,
            letterSpacing: '0.04em',
          }}>
            LIVE
          </span>
        )}
      </div>

      {/* ── Fetch error ─────────────────────────────────────────────────── */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            style={{
              border:       '1px solid rgba(255,68,68,0.28)',
              borderRadius: 5,
              padding:      '6px 10px',
              marginBottom: 10,
              fontFamily:   'var(--fmo)',
              fontSize:     11,
              color:        '#FF6B6B',
              letterSpacing:'0.02em',
            }}
          >
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Body ────────────────────────────────────────────────────────── */}
      {loading ? (
        <div>
          <SkeletonRow delay={0}    width={260} />
          <SkeletonRow delay={0.07} width={160} />
        </div>

      ) : data?.status === 'never_applied' ? (
        /* Never applied — flat empty state */
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          style={{
            padding:   '10px 0 6px',
            display:   'flex',
            flexDirection: 'column',
            gap:       6,
          }}
        >
          <span className="mono-num" style={{
            fontSize: 11, color: 'rgba(255,255,255,0.22)', letterSpacing: '0.04em',
          }}>
            — titolo shop non ancora ottimizzato —
          </span>
          <PreviewButton loading={previewing} open={previewOpen} onClick={() => { void handlePreview() }} />
        </motion.div>

      ) : data ? (
        /* Applied state */
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          style={{ display: 'flex', flexDirection: 'column', gap: 0 }}
        >
          {/* Optimization score bar */}
          {data.status === 'applied' && (
            <OptimizationScore data={data} />
          )}

          {/* Title row */}
          <div style={{
            display:      'flex',
            alignItems:   'baseline',
            gap:          10,
            padding:      '7px 0',
            borderBottom: '1px solid rgba(255,255,255,0.05)',
          }}>
            <span className="hud-label" style={{ fontSize: 9, minWidth: 80, flexShrink: 0 }}>
              Titolo applicato
            </span>
            <span className="mono-num" style={{
              fontSize:     11,
              color:        'rgba(255,255,255,0.65)',
              lineHeight:   1.45,
              overflow:     'hidden',
              textOverflow: 'ellipsis',
              whiteSpace:   'nowrap',
              minWidth:     0,
            }}>
              "{data.last_title}"
            </span>
          </div>

          {/* Niches row */}
          {data.last_niches.length > 0 && (
            <div style={{
              display:      'flex',
              alignItems:   'baseline',
              gap:          10,
              padding:      '7px 0',
              borderBottom: '1px solid rgba(255,255,255,0.05)',
            }}>
              <span className="hud-label" style={{ fontSize: 9, minWidth: 80, flexShrink: 0 }}>
                Niches
              </span>
              <span className="mono-num" style={{
                fontSize:  10,
                color:     'rgba(255,255,255,0.42)',
                lineHeight: 1.5,
              }}>
                {data.last_niches.join(', ')}
              </span>
            </div>
          )}

          {/* Preview CTA button + panel */}
          <div style={{ paddingTop: 10 }}>
            <PreviewButton loading={previewing} open={previewOpen} onClick={() => { void handlePreview() }} />

            {/* Preview error */}
            <AnimatePresence>
              {previewErr && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  style={{
                    border:       '1px solid rgba(255,68,68,0.28)',
                    borderRadius: 5,
                    padding:      '6px 10px',
                    marginTop:    8,
                    fontFamily:   'var(--fmo)',
                    fontSize:     11,
                    color:        '#FF6B6B',
                    letterSpacing:'0.02em',
                  }}
                >
                  {previewErr}
                </motion.div>
              )}
            </AnimatePresence>

            {/* Preview result panel */}
            <AnimatePresence initial={false}>
              {previewOpen && preview && (
                <PreviewPanel result={preview} />
              )}
            </AnimatePresence>

            {/* Suggestions section — shown after preview */}
            <AnimatePresence>
              {previewOpen && preview && (
                <motion.div
                  key="suggestions"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ type: 'spring', stiffness: 240, damping: 28, delay: 0.15 }}
                  style={{ overflow: 'hidden', marginTop: 10 }}
                >
                  <div className="hud-label" style={{ fontSize: 8, marginBottom: 7 }}>
                    SUGGESTIONS
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.42)' }}>
                        Title suggestions
                      </span>
                      <span className="mono-num" style={{ fontSize: 12, color: '#B57BFF', fontWeight: 600 }}>
                        {preview.title ? 1 : 0}
                      </span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.42)' }}>
                        Niche suggestions
                      </span>
                      <span className="mono-num" style={{ fontSize: 12, color: '#B57BFF', fontWeight: 600 }}>
                        {preview.niches.length}
                      </span>
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </motion.div>

      ) : null}
    </div>
  )
}
