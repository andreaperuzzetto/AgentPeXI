import { useState, useMemo } from 'react'
import { useStore } from '../../store'
import type { AgentStep } from '../../types'
import './OrbOverlay.css'

/* ── types ─────────────────────────────────────────────────── */
type Filter = 'tutti' | 'tool' | 'llm' | 'think'

const FILTERS: { key: Filter; label: string }[] = [
  { key: 'tutti', label: 'Tutti' },
  { key: 'tool',  label: 'Tool'  },
  { key: 'llm',   label: 'LLM'   },
  { key: 'think', label: 'Think' },
]

/* ── helpers ────────────────────────────────────────────────── */
function fmtTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function fmtDuration(ms: number): string {
  if (ms <= 0)      return ''
  if (ms < 1_000)   return `${ms}ms`
  return `${(ms / 1_000).toFixed(1)}s`
}

function tagType(stepType: string): 'tool' | 'llm' | 'think' {
  if (stepType === 'tool') return 'tool'
  if (stepType === 'llm')  return 'llm'
  return 'think'
}

/* ── sub-components ─────────────────────────────────────────── */
function FilterPill({
  active, label, onClick,
}: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      className={`sd-pill${active ? ' active' : ''}`}
      onClick={onClick}
    >
      {label}
    </button>
  )
}

function StepRow({ step }: { step: AgentStep }) {
  const tag = tagType(step.stepType)
  return (
    <div className="sd-step">
      <span className={`sgc-tag ${tag}`}>{tag}</span>
      <div className="sd-step-body">
        <div className="sd-step-agent">{step.agent}</div>
        <div className="sd-step-desc">{step.description}</div>
        <div className="sd-step-meta">
          {fmtTime(step.timestamp)}
          {step.durationMs > 0 && ` · ${fmtDuration(step.durationMs)}`}
        </div>
      </div>
    </div>
  )
}

/* ── component ─────────────────────────────────────────────── */
interface StepDrawerProps {
  open: boolean
  onToggle: () => void
}

export function StepDrawer({ open, onToggle }: StepDrawerProps) {
  const agentSteps = useStore((s) => s.agentSteps)
  const [filter, setFilter] = useState<Filter>('tutti')

  /* flatten + sort descending (newest first) */
  const allSteps = useMemo<AgentStep[]>(() => {
    const flat = Object.values(agentSteps).flat()
    flat.sort((a, b) => (b.timestamp ? new Date(b.timestamp).getTime() : 0) - (a.timestamp ? new Date(a.timestamp).getTime() : 0))
    return flat
  }, [agentSteps])

  const filtered = useMemo<AgentStep[]>(() => {
    if (filter === 'tutti') return allSteps
    return allSteps.filter((s) => tagType(s.stepType) === filter)
  }, [allSteps, filter])

  /* count per type for pills */
  const counts = useMemo(() => ({
    tutti: allSteps.length,
    tool:  allSteps.filter((s) => tagType(s.stepType) === 'tool').length,
    llm:   allSteps.filter((s) => tagType(s.stepType) === 'llm').length,
    think: allSteps.filter((s) => tagType(s.stepType) === 'think').length,
  }), [allSteps])

  return (
    <>
      {/* ── toggle button on left edge — arrow only ── */}
      <button
        className={`sd-toggle${open ? ' active' : ''}`}
        onClick={onToggle}
        title={open ? 'Chiudi storico' : 'Apri storico'}
      >
        {open ? '‹' : '›'}
      </button>

      {/* ── drawer panel ── */}
      <div className={`step-drawer${open ? ' open' : ''}`}>
        {/* head */}
        <div className="sd-head">
          <span className="sd-title">Storico step</span>
          <button className="sd-close" onClick={onToggle}>✕</button>
        </div>

        {/* filter pills */}
        <div className="sd-pills">
          {FILTERS.map(({ key, label }) => (
            <FilterPill
              key={key}
              active={filter === key}
              label={`${label}${counts[key] > 0 ? ` ${counts[key]}` : ''}`}
              onClick={() => setFilter(key)}
            />
          ))}
          <span className="sd-count">{filtered.length} step</span>
        </div>

        {/* scroll area */}
        <div className="sd-scroll">
          {filtered.length === 0 ? (
            <div style={{
              fontFamily: 'var(--fmo)',
              fontSize: 10,
              color: 'var(--tf)',
              textAlign: 'center',
              paddingTop: 24,
              letterSpacing: '.06em',
            }}>
              Nessun step
            </div>
          ) : (
            filtered.map((step) => <StepRow key={step.id} step={step} />)
          )}
        </div>
      </div>
    </>
  )
}
