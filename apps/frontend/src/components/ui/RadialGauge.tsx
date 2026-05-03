/**
 * RadialGauge — gauge SVG radiale riutilizzabile.
 *
 * Arco di 270° (3/4 cerchio), gap in basso al centro.
 * Fill animato con spring Framer Motion su strokeDashoffset.
 * Colore diventa #FF4444 se pct >= warnAt (default 80).
 *
 * Usato in: BudgetGauges (LLM, Image, Fee)
 */

import { motion } from 'framer-motion'

/* ── Warning threshold ────────────────────────────────────────────────────── */
const DEFAULT_WARN = 80

interface RadialGaugeProps {
  /** Label sopra il valore (es. "LLM") */
  label:      string
  /** Valore assoluto da mostrare sotto il label (es. "$0.42") */
  valueStr:   string
  /** 0-100 percentuale di fill */
  pct:        number
  /** Colore base del fill (es. '#F5A623') — override se pct >= warnAt */
  color:      string
  /** Dimensione SVG in px — default 120 */
  size?:      number
  /** Spessore arco in px — default 5 */
  strokeW?:   number
  /** Soglia warning — default 80 */
  warnAt?:    number
  /** Testo piccolo sotto il valore (es. "di €1.20/g") */
  subLabel?:  string
  /** Mount stagger delay in secondi */
  delay?:     number
}

export function RadialGauge({
  label,
  valueStr,
  pct,
  color,
  size    = 120,
  strokeW = 5,
  warnAt  = DEFAULT_WARN,
  subLabel,
  delay   = 0,
}: RadialGaugeProps) {
  const clampedPct   = Math.max(0, Math.min(100, pct))
  const isWarning    = pct >= warnAt
  const fillColor    = isWarning ? '#FF4444' : color

  /* ── Arc geometry ── */
  const cx           = size / 2
  const cy           = size / 2
  const r            = size / 2 - strokeW - 8      // inner padding
  const C            = 2 * Math.PI * r             // full circumference
  const trackLen     = C * 0.75                    // 270° = 75% of C
  const fillLen      = trackLen * (clampedPct / 100)
  const emptyOffset  = trackLen                    // initial = nothing shown
  const targetOffset = trackLen - fillLen          // final = pct% shown

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ type: 'spring', stiffness: 260, damping: 28, delay }}
      style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}
    >
      {/* ── SVG gauge ─────────────────────────────────────────────────── */}
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        style={{ overflow: 'visible' }}
      >
        {/* Track arc — always shows full 270° in dim color */}
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke="rgba(255,255,255,0.07)"
          strokeWidth={strokeW}
          strokeDasharray={`${trackLen} ${C}`}
          strokeLinecap="round"
          transform={`rotate(135 ${cx} ${cy})`}
        />

        {/* Fill arc — animated */}
        <motion.circle
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke={fillColor}
          strokeWidth={strokeW}
          strokeDasharray={`${trackLen} ${C}`}
          strokeLinecap="round"
          transform={`rotate(135 ${cx} ${cy})`}
          initial={{ strokeDashoffset: emptyOffset }}
          animate={{ strokeDashoffset: targetOffset }}
          transition={{
            type:      'spring',
            stiffness: 70,
            damping:   18,
            delay:     delay + 0.1,
          }}
          style={{
            /* Glow effect on the fill arc */
            filter: isWarning
              ? 'drop-shadow(0 0 4px rgba(255,68,68,0.55))'
              : `drop-shadow(0 0 4px ${color}55)`,
          }}
        />

        {/* ── Center text ────────────────────────────────────────────── */}
        {/* Label */}
        <text
          x={cx}
          y={cy - size * 0.10}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            fontFamily:    '"JetBrains Mono", monospace',
            fontSize:      size * 0.085,
            letterSpacing: '0.12em',
            fill:          'rgba(255,255,255,0.38)',
            textTransform: 'uppercase',
          }}
        >
          {label}
        </text>

        {/* Value */}
        <text
          x={cx}
          y={cy + size * 0.08}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            fontFamily:    '"JetBrains Mono", monospace',
            fontSize:      size * 0.155,
            fontWeight:    500,
            fill:          isWarning ? '#FF4444' : 'rgba(255,255,255,0.85)',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {valueStr}
        </text>

        {/* Pct */}
        <text
          x={cx}
          y={cy + size * 0.26}
          textAnchor="middle"
          dominantBaseline="middle"
          style={{
            fontFamily:    '"JetBrains Mono", monospace',
            fontSize:      size * 0.080,
            fill:          isWarning ? 'rgba(255,68,68,0.70)' : 'rgba(255,255,255,0.28)',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {pct > 0 ? `${Math.round(pct)}%` : '—'}
        </text>
      </svg>

      {/* Sub-label under the SVG */}
      {subLabel && (
        <span
          className="mono-num"
          style={{
            fontSize:      9,
            color:         'rgba(255,255,255,0.22)',
            letterSpacing: '0.05em',
            marginTop:     -4,
            textAlign:     'center',
          }}
        >
          {subLabel}
        </span>
      )}
    </motion.div>
  )
}
