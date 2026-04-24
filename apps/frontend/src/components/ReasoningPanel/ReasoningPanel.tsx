import { useRef, useEffect, useMemo, useState } from 'react'
import { useStore } from '../../store'
import type { AgentStep } from '../../types/index'

/* ── helpers ────────────────────────────────────────────────── */
function relTime(iso: string | undefined | null): string {
  if (!iso) return ''
  try {
    const t = new Date(iso).getTime()
    if (isNaN(t)) return ''
    const s = Math.round((Date.now() - t) / 1000)
    if (s < 60)  return `${s}s`
    const m = Math.round(s / 60)
    if (m < 60)  return `${m}m`
    return `${Math.round(m / 60)}h`
  } catch { return '' }
}

// Normalize step type to a CSS-safe token
function typeClass(t: string): 'tool' | 'llm' | 'think' {
  if (t === 'tool')  return 'tool'
  if (t === 'llm')   return 'llm'
  return 'think'
}

/* ── pill config ─────────────────────────────────────────────── */
const PILLS: { key: string; label: string }[] = [
  { key: 'all',   label: 'Tutti' },
  { key: 'tool',  label: 'Tool'  },
  { key: 'llm',   label: 'LLM'   },
  { key: 'think', label: 'Think' },
]

/* ── component ───────────────────────────────────────────────── */
export function ReasoningPanel() {
  const agentSteps              = useStore((s) => s.agentSteps)
  const [activeFilter, setFilter] = useState<string>('all')
  const scrollRef               = useRef<HTMLDivElement>(null)

  // Flatten + sort by timestamp, keep last 80
  const allSteps: AgentStep[] = useMemo(() => {
    const flat = Object.values(agentSteps).flat()
    flat.sort((a, b) => (a.timestamp ? new Date(a.timestamp).getTime() : 0) - (b.timestamp ? new Date(b.timestamp).getTime() : 0))
    return flat.slice(-80)
  }, [agentSteps])

  // Apply filter
  const steps: AgentStep[] = useMemo(() => {
    if (activeFilter === 'all') return allSteps
    return allSteps.filter((s) => typeClass(s.stepType) === activeFilter)
  }, [allSteps, activeFilter])

  // Auto-scroll to bottom on new content
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [steps.length])

  return (
    <>
      {/* ── Scrollable step feed ── */}
      <div className="reasoning-scroll" ref={scrollRef}>
        {steps.length === 0 ? (
          <div style={{
            height: '100%',
            display: 'flex',
            alignItems: 'flex-end',
            paddingBottom: 6,
          }}>
            <span style={{
              fontFamily: 'var(--fmo)',
              fontSize: 10,
              color: 'var(--tf)',
              letterSpacing: '.06em',
              opacity: .5,
            }}>
              in attesa…
            </span>
          </div>
        ) : (
          steps.map((step, i) => {
            const tc = typeClass(step.stepType)
            const isLast = i === steps.length - 1
            return (
              <div key={step.id} className="step-card">

                {/* left col: agent name + type badge */}
                <div className="sc-left">
                  <span className={`sc-agent ${tc}`}>
                    {step.agent.toUpperCase().slice(0, 7)}
                  </span>
                  <span className={`sc-typetag ${tc}`}>{tc}</span>
                </div>

                {/* body: description + meta */}
                <div className="sc-body">
                  <div className={`sc-desc${isLast ? ' last' : ''}`}>
                    {step.description}
                  </div>
                  <div className="sc-meta">
                    {step.taskId ? step.taskId.slice(0, 8) : '—'} · {step.durationMs > 0 ? `${step.durationMs}ms` : relTime(step.timestamp)}
                  </div>
                </div>

                {/* timestamp */}
                <span className="sc-time">{relTime(step.timestamp)}</span>
              </div>
            )
          })
        )}
      </div>

      {/* ── Filter pills row ── */}
      <div className="reasoning-pills">
        {PILLS.map((p) => (
          <button
            key={p.key}
            className={`r-pill${activeFilter === p.key ? ' active' : ''}`}
            onClick={() => setFilter(p.key)}
          >
            {p.label}
          </button>
        ))}
        <span className="r-pill-spacer" />
        <span className="r-count">{steps.length} step</span>
      </div>
    </>
  )
}
