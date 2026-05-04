/**
 * AgentCard — FE-Blocco 6
 *
 * Card densa (GlassCard hudVariant) per singolo agente o servizio.
 * Densità 7/10 — padding 12px, gap interni 6-8px.
 *
 * Status badge: pastello desaturato (spec FE-6):
 *   running → rgba(27,255,94,0.12)
 *   error   → rgba(255,68,68,0.12)
 *   idle    → rgba(255,255,255,0.05)
 */

import type { CSSProperties } from 'react'
import { GlassCard } from '../ui/GlassCard'

export type AgentLayer = 'business' | 'personal' | 'service'

export interface AgentCardProps {
  name:              string
  layer:             AgentLayer
  status:            'idle' | 'running' | 'error'
  model?:            string    // LLM model — omesso per servizi
  lastTask?:         string    // testo troncato
  stepsToday:        number
  pipelinePosition?: string    // es. "1 · research"
  isService?:        boolean   // badge [ SVC ] al posto del modello
  onClick?:          () => void
  style?:            CSSProperties
}

// ─── Static model map ──────────────────────────────────────────────────────────
export const AGENT_MODELS: Record<string, string> = {
  // Business
  research:          'claude-sonnet',
  design:            'claude-sonnet',
  publisher:         'claude-sonnet',
  analytics:         'claude-sonnet',
  finance:           'claude-sonnet',
  market_data:       'claude-haiku',
  // Personal
  recall:            'claude-haiku',
  remind:            'claude-haiku',
  summarize:         'claude-haiku',
  research_personal: 'claude-sonnet',
  watcher:           'ollama',
}

// ─── Status badge styles ───────────────────────────────────────────────────────
function statusBadgeStyle(status: AgentCardProps['status']): CSSProperties {
  if (status === 'running') {
    return {
      background:  'rgba(27,255,94,0.12)',
      color:       'rgba(27,255,94,0.85)',
      border:      '1px solid rgba(27,255,94,0.20)',
    }
  }
  if (status === 'error') {
    return {
      background:  'rgba(255,68,68,0.12)',
      color:       'rgba(255,68,68,0.85)',
      border:      '1px solid rgba(255,68,68,0.20)',
    }
  }
  return {
    background:  'rgba(255,255,255,0.05)',
    color:       'var(--tf)',
    border:      '1px solid rgba(255,255,255,0.06)',
  }
}

// ─── Layer accent colors ───────────────────────────────────────────────────────
const LAYER_COLOR: Record<AgentLayer, string> = {
  business: 'var(--zone-etsy)',
  personal: 'var(--zone-personal)',
  service:  'var(--zone-system)',
}

// ─── AgentCard ─────────────────────────────────────────────────────────────────
export function AgentCard({
  name,
  layer,
  status,
  model,
  lastTask,
  stepsToday,
  pipelinePosition,
  isService = false,
  onClick,
  style,
}: AgentCardProps) {
  const accentColor   = LAYER_COLOR[layer]
  const badgeStyle    = statusBadgeStyle(status)
  const resolvedModel = model ?? AGENT_MODELS[name]
  const displayName   = name.replace(/_/g, ' ')
  const taskText      = lastTask && lastTask.length > 62
    ? lastTask.slice(0, 62) + '…'
    : lastTask

  return (
    <GlassCard
      hudVariant
      className={onClick ? 'ac-clickable-card' : ''}
      style={{
        cursor: onClick ? 'pointer' : undefined,
        height: '100%',
        ...style,
      }}
      innerStyle={{ padding: 12, display: 'flex', flexDirection: 'column', height: '100%', boxSizing: 'border-box' }}
    >
      {/* Clickable wrapper */}
      <div
        style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}
        onClick={onClick}
      >
        {/* ── Header: name + status badge ─────────────────────────── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          {/* Status dot */}
          <span style={{
            width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
            background: status === 'running'
              ? 'rgba(27,255,94,0.85)'
              : status === 'error'
                ? 'rgba(255,68,68,0.85)'
                : 'var(--tf)',
            boxShadow: status === 'running' ? '0 0 6px rgba(27,255,94,0.5)' : 'none',
          }} />

          {/* Agent name */}
          <span style={{
            fontFamily:    'var(--fui)',
            fontSize:      12,
            fontWeight:    600,
            letterSpacing: '0.04em',
            color:         accentColor,
            flex:          1,
            minWidth:      0,
            overflow:      'hidden',
            textOverflow:  'ellipsis',
            whiteSpace:    'nowrap',
            textTransform: 'capitalize',
          }}>
            {displayName}
          </span>

          {/* Status badge */}
          <span style={{
            fontFamily:    'var(--fmo)',
            fontSize:      10,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            padding:       '2px 6px',
            borderRadius:  3,
            flexShrink:    0,
            ...badgeStyle,
          }}>
            {status}
          </span>
        </div>

        {/* ── Model / Service badge + pipeline ────────────────────── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
          {isService ? (
            <span style={{
              fontFamily:    'var(--fmo)',
              fontSize:      10,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              padding:       '1px 5px',
              borderRadius:  3,
              background:    'rgba(107,114,128,0.15)',
              color:         'var(--zone-system)',
              border:        '1px solid rgba(107,114,128,0.20)',
            }}>
              SVC
            </span>
          ) : resolvedModel ? (
            <span style={{
              fontFamily:    'var(--fmo)',
              fontSize:      10,
              letterSpacing: '0.06em',
              color:         'var(--tf)',
            }}>
              {resolvedModel}
            </span>
          ) : null}

          {pipelinePosition && (
            <span style={{
              fontFamily:    'var(--fmo)',
              fontSize:      10,
              letterSpacing: '0.06em',
              color:         'var(--tf)',
              marginLeft:    'auto',
            }}>
              {pipelinePosition}
            </span>
          )}
        </div>

        {/* ── Last task — grows to fill available space ────────────── */}
        <div style={{
          fontFamily:      'var(--fmo)',
          fontSize:        11,
          color:           taskText ? 'var(--tm)' : 'var(--tf)',
          lineHeight:      1.4,
          flex:            1,
          overflow:        'hidden',
          display:         '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
        }}>
          {taskText || <span style={{ opacity: 0.5 }}>—</span>}
        </div>

        {/* ── Step count ──────────────────────────────────────────── */}
        <div style={{
          marginTop:     8,
          paddingTop:    6,
          borderTop:     '1px solid rgba(255,255,255,0.05)',
          fontFamily:    'var(--fmo)',
          fontSize:      10,
          color:         'var(--tf)',
          letterSpacing: '0.04em',
          flexShrink:    0,
        }}>
          <span className="mono-num">
            {stepsToday}
          </span>
          {' '}step oggi
        </div>
      </div>
    </GlassCard>
  )
}
