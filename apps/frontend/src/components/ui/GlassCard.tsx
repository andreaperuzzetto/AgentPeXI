/**
 * GlassCard — Double-Bezel (Doppelrand) primitive
 *
 * Two variants:
 *  - default: opaque --bg-s1 inner, rounded-3xl radii (cards in views)
 *  - hudVariant: 0.75rem radii, semi-transparent inner with backdrop-filter
 *    for panels floating over the Three.js canvas
 *
 * Canonical structure from PLAN_FE §Design System:
 *   outer shell: 2px padding, subtle border, 2% white tint
 *   inner core:  rounded, background, top-rim inset light
 */

import type { CSSProperties, ReactNode } from 'react'

interface GlassCardProps {
  children:       ReactNode
  /** Additional class names on the outer shell */
  className?:     string
  /** Smaller border-radius + HUD-density padding */
  hudVariant?:    boolean
  /** Override outer shell style */
  style?:         CSSProperties
  /** Override inner core style */
  innerStyle?:    CSSProperties
  /** Class names on the inner core element */
  innerClassName?: string
}

export function GlassCard({
  children,
  className      = '',
  hudVariant     = false,
  style,
  innerStyle,
  innerClassName = '',
}: GlassCardProps) {
  const r = hudVariant ? '0.75rem' : '1.5rem'
  const ri = `calc(${r} - 2px)`

  const outerStyle: CSSProperties = hudVariant
    ? {
        borderRadius: r,
        border:       '1px solid rgba(255,255,255,0.09)',
        background:   'var(--bg-s1)',
        overflow:     'hidden',
        ...style,
      }
    : {
        padding:      2,
        borderRadius: r,
        border:       '1px solid rgba(255,255,255,0.09)',
        background:   'rgba(255,255,255,0.03)',
        ...style,
      }

  const defaultInner: CSSProperties = hudVariant
    ? {
        borderRadius: r,
        background:   'rgba(13,15,18,0.72)',
        boxShadow:    'inset 0 1px 0 rgba(255,255,255,0.08)',
        overflow:     'hidden',
      }
    : {
        borderRadius: ri,
        background:   'var(--bg-s1)',
        boxShadow:    'inset 0 1px 0 rgba(255,255,255,0.10)',
        overflow:     'hidden',
      }

  return (
    <div className={className} style={outerStyle}>
      <div
        className={innerClassName}
        style={{ ...defaultInner, ...innerStyle }}
      >
        {children}
      </div>
    </div>
  )
}
