/**
 * PipelineBar — barra segmentata orizzontale proporzionale ai conteggi.
 *
 * Ogni segmento: key, label, count, color.
 * Segmenti con count=0 sono nascosti dalla barra (ma presenti nel legend).
 * Mount animation: ogni segmento entra con scaleX spring staggered.
 *
 * Usato in: ProductionPipeline, ShopOptimizerCard (futuro)
 */

import { motion } from 'framer-motion'

export interface PipelineSegment {
  key:   string
  label: string
  count: number
  color: string
}

interface PipelineBarProps {
  segments:    PipelineSegment[]
  showCounts?: boolean    // default true — mostra count + label sotto la barra
  height?:     number     // default 8
}

export function PipelineBar({ segments, showCounts = true, height = 8 }: PipelineBarProps) {
  const total    = segments.reduce((s, seg) => s + seg.count, 0)
  const nonEmpty = segments.filter(s => s.count > 0)

  /* ── Empty state ── */
  if (total === 0) {
    return (
      <div style={{
        height:       height,
        borderRadius: 4,
        background:   'rgba(255,255,255,0.05)',
      }} />
    )
  }

  return (
    <div>
      {/* ── Bar ───────────────────────────────────────────────────────────── */}
      <div style={{
        display:      'flex',
        height:       height,
        borderRadius: height / 2,
        overflow:     'hidden',
        gap:          1,
        background:   'rgba(255,255,255,0.04)',
      }}>
        {nonEmpty.map((seg, i) => {
          const isFirst = i === 0
          const isLast  = i === nonEmpty.length - 1
          const r       = height / 2
          const br      = [
            isFirst ? r : 0,
            isLast  ? r : 0,
            isLast  ? r : 0,
            isFirst ? r : 0,
          ].map(v => `${v}px`).join(' ')

          return (
            <motion.div
              key={seg.key}
              initial={{ scaleX: 0, opacity: 0 }}
              animate={{ scaleX: 1, opacity: 1 }}
              transition={{
                scaleX:  { type: 'spring', stiffness: 140, damping: 22, delay: i * 0.07 },
                opacity: { duration: 0.2, delay: i * 0.07 },
              }}
              style={{
                transformOrigin: 'left center',
                flex:            seg.count,
                background:      seg.color,
                borderRadius:    br,
                opacity:         0.82,
              }}
            />
          )
        })}
      </div>

      {/* ── Count labels ──────────────────────────────────────────────────── */}
      {showCounts && (
        <div style={{
          display:   'flex',
          marginTop: 7,
          gap:       1,
        }}>
          {segments.map((seg) => (
            seg.count === 0 ? null : (
              <div
                key={seg.key}
                style={{
                  flex:          seg.count,
                  display:       'flex',
                  flexDirection: 'column',
                  gap:           1,
                  minWidth:      0,
                }}
              >
                <span
                  className="mono-num"
                  style={{
                    fontSize:      9,
                    color:         seg.color,
                    opacity:       0.65,
                    letterSpacing: '0.06em',
                    textTransform: 'uppercase',
                    whiteSpace:    'nowrap',
                    overflow:      'hidden',
                    textOverflow:  'ellipsis',
                    lineHeight:    1.3,
                  }}
                >
                  {seg.label}
                </span>
                <span
                  className="mono-num"
                  style={{
                    fontSize:      12,
                    fontWeight:    500,
                    color:         'rgba(255,255,255,0.72)',
                    lineHeight:    1.2,
                  }}
                >
                  {seg.count}
                </span>
              </div>
            )
          ))}
        </div>
      )}
    </div>
  )
}
