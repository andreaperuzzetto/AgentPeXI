/**
 * HudPanel — floating HUD panel with ASCII bracket title
 *
 * Wraps GlassCard (hudVariant) with a standard [ TITLE ] header.
 * Position (top/left/right/bottom) must be set by the parent via style prop.
 *
 * Title format: [ MEMORY STREAMS ], [ COLLECTIONS ], [ BRIDGE ], [ STEP FEED ]
 * Per PLAN_FE §3 Skill directives.
 */

import type { CSSProperties, ReactNode } from 'react'
import { GlassCard } from './GlassCard'

interface HudPanelProps {
  title:    ReactNode
  children: ReactNode
  style?:   CSSProperties
  padding?: string
}

export function HudPanel({ title, children, style, padding = '10px 12px 8px' }: HudPanelProps) {
  return (
    <GlassCard
      hudVariant
      style={{ position: 'absolute', ...style }}
      innerStyle={{ padding }}
    >
      <div className="hud-label" style={{ marginBottom: 10 }}>
        [ {title} ]
      </div>

      {children}
    </GlassCard>
  )
}
