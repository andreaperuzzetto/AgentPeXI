/**
 * BundleStatus — niches bundle-ready con spec espandibile.
 *
 * FE-Blocco 4, Step 4.4
 *
 * Dati: GET /api/etsy/bundles (cache 10 min server-side)
 * Fetch: on mount, poi ogni 5 min (cache lato server già gestita)
 *
 * Layout per bundle:
 *   [ niche ]            score: 0.84  ·  3 comp  ·  €12.99 stim.   [ Spec → ]
 *   ↳ (accordion) component titles · keywords · prezzo dettaglio
 *
 * States:
 *   loading → skeleton 2 item
 *   empty   → float perpetuo + "Analisi bundle in corso..."
 *   live    → lista bundle con accordion spec
 *   error   → inline error
 */

import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence }           from 'framer-motion'
import type { BundleItem }                   from '../../types'

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function fmtPrice(p: number): string {
  if (p <= 0) return '—'
  return `€${p.toFixed(2)}`
}

function fmtCacheAge(ts: number | null): string {
  if (!ts) return ''
  const ageMin = Math.round((Date.now() / 1000 - ts) / 60)
  if (ageMin < 1) return 'live'
  return `cache ${ageMin}m fa`
}

/* ── Skeleton item ────────────────────────────────────────────────────────── */
function SkeletonItem({ delay = 0 }: { delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay, duration: 0.28 }}
      style={{
        display:       'flex',
        flexDirection: 'column',
        gap:           6,
        padding:       '10px 0',
        borderBottom:  '1px solid rgba(255,255,255,0.05)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ height: 14, width: 130, borderRadius: 3, background: 'rgba(255,255,255,0.08)' }} />
        <div style={{ height: 9,  flex: 1,   borderRadius: 3, background: 'rgba(255,255,255,0.04)' }} />
        <div style={{ height: 20, width: 68,  borderRadius: 4, background: 'rgba(255,255,255,0.06)' }} />
      </div>
      <div style={{ height: 8, width: '55%', borderRadius: 3, background: 'rgba(255,255,255,0.04)' }} />
    </motion.div>
  )
}

/* ── Bundle row with accordion ────────────────────────────────────────────── */
interface BundleRowProps {
  item:   BundleItem
  index:  number
  isLast: boolean
}

function BundleRow({ item, index, isLast }: BundleRowProps) {
  const [open, setOpen] = useState(false)
  const { spec } = item

  /* Truncate long keyword lists */
  const kwPreview = spec.keywords.slice(0, 6).join(', ') + (spec.keywords.length > 6 ? '…' : '')

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, transition: { duration: 0.12 } }}
      transition={{ type: 'spring', stiffness: 320, damping: 30, delay: index * 0.06 }}
      style={{
        borderBottom: isLast && !open ? 'none' : '1px solid rgba(255,255,255,0.05)',
      }}
    >
      {/* ── Summary row ───────────────────────────────────────────────── */}
      <div style={{
        display:     'flex',
        alignItems:  'center',
        gap:         10,
        padding:     '10px 0',
      }}>
        {/* Niche label */}
        <span
          className="mono-num"
          style={{
            fontSize:      11,
            fontWeight:    500,
            color:         '#F5A623',
            background:    'rgba(245,166,35,0.10)',
            padding:       '3px 7px',
            borderRadius:  3,
            letterSpacing: '0.03em',
            whiteSpace:    'nowrap',
            flexShrink:    0,
          }}
        >
          [{item.niche}]
        </span>

        {/* Stats */}
        <div style={{ flex: 1, display: 'flex', gap: 14, alignItems: 'center', minWidth: 0 }}>
          <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.40)', whiteSpace: 'nowrap' }}>
            score{' '}
            <span style={{ color: 'rgba(255,255,255,0.65)' }}>{item.score.toFixed(2)}</span>
          </span>
          <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.28)' }}>·</span>
          <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.40)', whiteSpace: 'nowrap' }}>
            <span style={{ color: 'rgba(255,255,255,0.65)' }}>{spec.n_components}</span>
            {' '}comp
          </span>
          <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.28)' }}>·</span>
          <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.40)', whiteSpace: 'nowrap' }}>
            <span style={{
              color: spec.suggested_price > 0 ? '#1BFF5E' : 'rgba(255,255,255,0.28)',
            }}>
              {fmtPrice(spec.suggested_price)}
            </span>
            {spec.suggested_price > 0 && (
              <span style={{ color: 'rgba(255,255,255,0.28)' }}> stim.</span>
            )}
          </span>
        </div>

        {/* Spec toggle button */}
        <motion.button
          onClick={() => setOpen(v => !v)}
          whileHover={{ scale: 1.03 }}
          whileTap={{ scale: 0.97, y: 1 }}
          transition={{ type: 'spring', stiffness: 400, damping: 20 }}
          style={{
            display:       'flex',
            alignItems:    'center',
            gap:           6,
            background:    open ? 'rgba(245,166,35,0.14)' : 'rgba(255,255,255,0.05)',
            border:        `1px solid ${open ? 'rgba(245,166,35,0.30)' : 'rgba(255,255,255,0.09)'}`,
            borderRadius:  4,
            padding:       '4px 10px',
            cursor:        'pointer',
            flexShrink:    0,
            transition:    'background 0.18s cubic-bezier(0.32,0.72,0,1), border-color 0.18s cubic-bezier(0.32,0.72,0,1)',
          }}
        >
          <span className="mono-num" style={{
            fontSize:      10,
            letterSpacing: '0.06em',
            color:         open ? '#F5A623' : 'rgba(255,255,255,0.55)',
            textTransform: 'uppercase',
          }}>
            Spec
          </span>
          {/* Arrow — button-in-button inner icon */}
          <motion.span
            animate={{ rotate: open ? 90 : 0 }}
            transition={{ type: 'spring', stiffness: 280, damping: 22 }}
            style={{
              display:       'inline-flex',
              alignItems:    'center',
              justifyContent:'center',
              width:         18,
              height:        18,
              borderRadius:  '50%',
              background:    'rgba(255,255,255,0.08)',
              fontSize:      10,
              color:         open ? '#F5A623' : 'rgba(255,255,255,0.45)',
              flexShrink:    0,
            }}
          >
            →
          </motion.span>
        </motion.button>
      </div>

      {/* ── Accordion spec panel ──────────────────────────────────────── */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="spec"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 260, damping: 28 }}
            style={{ overflow: 'hidden' }}
          >
            <div style={{
              background:   'rgba(255,255,255,0.025)',
              border:       '1px solid rgba(255,255,255,0.07)',
              borderRadius: 6,
              padding:      '10px 12px',
              marginBottom: 10,
              display:      'flex',
              flexDirection:'column',
              gap:          8,
            }}>
              {/* Suggested price — prominent */}
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span className="hud-label" style={{ fontSize: 9 }}>Prezzo bundle</span>
                <span className="mono-num" style={{
                  fontSize: 16, fontWeight: 600,
                  color: spec.suggested_price > 0 ? '#1BFF5E' : 'rgba(255,255,255,0.28)',
                }}>
                  {fmtPrice(spec.suggested_price)}
                </span>
                {spec.suggested_price > 0 && (
                  <span className="mono-num" style={{ fontSize: 10, color: 'rgba(255,255,255,0.28)' }}>
                    (70% of individual sum)
                  </span>
                )}
              </div>

              {/* Component titles */}
              {spec.component_titles.length > 0 && (
                <div>
                  <div className="hud-label" style={{ fontSize: 9, marginBottom: 5 }}>
                    Componenti ({spec.component_titles.length})
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                    {spec.component_titles.map((title, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                        <span className="mono-num" style={{
                          fontSize: 9, color: 'rgba(245,166,35,0.60)',
                          flexShrink: 0, minWidth: 16,
                        }}>
                          {i + 1}.
                        </span>
                        <span className="mono-num" style={{
                          fontSize: 10, color: 'rgba(255,255,255,0.60)',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {title}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Keywords preview */}
              {spec.keywords.length > 0 && (
                <div>
                  <div className="hud-label" style={{ fontSize: 9, marginBottom: 4 }}>
                    Keywords ({spec.keywords.length})
                  </div>
                  <span className="mono-num" style={{
                    fontSize: 10, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.01em',
                    lineHeight: 1.5,
                  }}>
                    {kwPreview}
                  </span>
                </div>
              )}

              {/* n_listings qualifier */}
              <div className="mono-num" style={{
                fontSize: 9, color: 'rgba(255,255,255,0.22)', letterSpacing: '0.03em',
                paddingTop: 2, borderTop: '1px solid rgba(255,255,255,0.05)',
              }}>
                Basato su {item.n_listings} listing pubblicati nell'ultimo mese
                {spec.pod_companion_type && (
                  <span style={{ color: 'rgba(181,123,255,0.60)' }}> · POD companion ready</span>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

/* ── BundleStatus ─────────────────────────────────────────────────────────── */
export function BundleStatus() {
  const [bundles,  setBundles]  = useState<BundleItem[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [cachedAt, setCachedAt] = useState<number | null>(null)

  const fetchBundles = useCallback(async () => {
    try {
      const res = await fetch('/api/etsy/bundles')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { bundles: BundleItem[]; cached_at: number | null }
      setBundles(data.bundles ?? [])
      setCachedAt(data.cached_at ?? null)
      setError(null)
    } catch {
      setError('Connessione fallita. Riprova.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchBundles()
    /* Polling ogni 5 min — il server ha cache 10 min */
    const id = setInterval(() => { void fetchBundles() }, 5 * 60_000)
    return () => clearInterval(id)
  }, [fetchBundles])

  return (
    <div style={{
      background:     'rgba(13,15,18,0.72)',
      border:         '1px solid rgba(255,255,255,0.07)',
      borderRadius:   10,
      padding:        '16px 18px 14px',
      backdropFilter: 'blur(12px)',
      boxShadow:      'inset 0 1px 0 rgba(255,255,255,0.07)',
    }}>
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
        <div className="hud-label" style={{ flex: 1 }}>
          [ BUNDLES READY ]
        </div>
        {!loading && cachedAt && (
          <span className="mono-num" style={{
            fontSize: 9, color: 'rgba(255,255,255,0.22)',
            letterSpacing: '0.04em',
          }}>
            {fmtCacheAge(cachedAt)}
          </span>
        )}
        {!loading && bundles.length > 0 && (
          <span className="mono-num" style={{
            fontSize: 10, color: '#1BFF5E',
            background: 'rgba(27,255,94,0.10)',
            padding: '2px 6px', borderRadius: 3,
            marginLeft: 8, letterSpacing: '0.04em',
          }}>
            {bundles.length}
          </span>
        )}
      </div>

      {/* ── Action buttons ──────────────────────────────────────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginBottom: 14 }}>
        <motion.button
          whileHover={{ scale: 1.015 }}
          whileTap={{ scale: 0.97, y: 1 }}
          transition={{ type: 'spring', stiffness: 400, damping: 22 }}
          style={{
            width: '100%', padding: '7px 0', border: 'none', borderRadius: 5, cursor: 'pointer',
            background: '#F5A623', color: '#0D0F12',
            fontFamily: 'var(--fmo)', fontSize: 10, fontWeight: 700,
            letterSpacing: '0.10em', textTransform: 'uppercase',
          }}
          onClick={() => window.open('/api/etsy/bundles', '_blank')}
        >
          GENERATE BUNDLE
        </motion.button>
        <motion.button
          whileHover={{ scale: 1.015 }}
          whileTap={{ scale: 0.97, y: 1 }}
          transition={{ type: 'spring', stiffness: 400, damping: 22 }}
          style={{
            width: '100%', padding: '7px 0',
            border: '1px solid rgba(245,166,35,0.30)', borderRadius: 5, cursor: 'pointer',
            background: 'transparent', color: 'rgba(245,166,35,0.75)',
            fontFamily: 'var(--fmo)', fontSize: 10, fontWeight: 600,
            letterSpacing: '0.10em', textTransform: 'uppercase',
          }}
        >
          VIEW ALL BUNDLES
        </motion.button>
        <motion.button
          whileHover={{ scale: 1.015 }}
          whileTap={{ scale: 0.97, y: 1 }}
          transition={{ type: 'spring', stiffness: 400, damping: 22 }}
          style={{
            width: '100%', padding: '7px 0',
            border: '1px solid rgba(255,255,255,0.07)', borderRadius: 5, cursor: 'pointer',
            background: 'transparent', color: 'rgba(255,255,255,0.30)',
            fontFamily: 'var(--fmo)', fontSize: 10, fontWeight: 500,
            letterSpacing: '0.10em', textTransform: 'uppercase',
          }}
        >
          PREVIEW ON ETSY
        </motion.button>
      </div>

      {/* ── Error ──────────────────────────────────────────────────────── */}
      {error && (
        <div style={{
          border: '1px solid rgba(255,68,68,0.28)', borderRadius: 5,
          padding: '6px 10px', marginBottom: 10,
          fontFamily: 'var(--fmo)', fontSize: 11, color: '#FF6B6B', letterSpacing: '0.02em',
        }}>
          {error}
        </div>
      )}

      {/* ── Body ───────────────────────────────────────────────────────── */}
      {loading ? (
        /* Skeleton — 2 item */
        <div>
          <SkeletonItem delay={0}    />
          <SkeletonItem delay={0.07} />
        </div>

      ) : bundles.length === 0 ? (
        /* Empty state */
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.35 }}
          style={{ padding: '18px 0', textAlign: 'center' }}
        >
          <motion.div
            animate={{ y: [-2, 2, -2] }}
            transition={{ repeat: Infinity, duration: 4, ease: 'easeInOut' }}
            style={{ fontSize: 20, marginBottom: 8, opacity: 0.22 }}
          >
            ⬡
          </motion.div>
          <span className="mono-num" style={{
            fontSize: 11, color: 'rgba(255,255,255,0.22)', letterSpacing: '0.04em',
          }}>
            — analisi bundle in corso —
          </span>
          <div className="mono-num" style={{
            fontSize: 9, color: 'rgba(255,255,255,0.14)',
            marginTop: 5, letterSpacing: '0.03em',
          }}>
            serve ≥{3} listing pubblicati per niche nell'ultimo mese
          </div>
        </motion.div>

      ) : (
        /* Live bundle list */
        <AnimatePresence initial={false} mode="popLayout">
          {bundles.map((item, i) => (
            <BundleRow
              key={item.niche}
              item={item}
              index={i}
              isLast={i === bundles.length - 1}
            />
          ))}
        </AnimatePresence>
      )}
    </div>
  )
}
