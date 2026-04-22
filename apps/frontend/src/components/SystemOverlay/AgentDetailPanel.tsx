import { useMemo, useEffect, useRef } from 'react'
import { useStore } from '../../store'

const EMPTY_STEPS: never[] = []

type StepType = 'tool' | 'llm' | 'think'

function stepTypeClass(t: string): StepType {
  if (t === 'tool_call' || t === 'tool') return 'tool'
  if (t === 'llm_call'  || t === 'llm')  return 'llm'
  return 'think'
}

const STEP_TAG_LABEL: Record<StepType, string> = {
  tool:  'TOOL',
  llm:   'LLM',
  think: 'THINK',
}

export function AgentDetailPanel() {
  const selectedAgent    = useStore((s) => s.selectedAgent)
  const setSelectedAgent = useStore((s) => s.setSelectedAgent)
  const addAgentStep     = useStore((s) => s.addAgentStep)
  const agent = useStore((s) => s.agents[selectedAgent ?? ''])
  const steps = useStore((s) => s.agentSteps[selectedAgent ?? ''] ?? EMPTY_STEPS)
  const reversedSteps = useMemo(() => [...steps].reverse(), [steps])

  // Fetch steps for this agent from backend if store is empty
  const fetchedRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!selectedAgent) return
    if (steps.length > 0) return                           // già popolato — non ri-fetcha
    if (fetchedRef.current.has(selectedAgent)) return     // già tentato in questa sessione
    fetchedRef.current.add(selectedAgent)

    fetch(`/api/agents/steps/recent?limit=100&agent_name=${encodeURIComponent(selectedAgent)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!Array.isArray(data?.steps)) return
        data.steps.forEach((s: {
          id: number; task_id: string; agent_name: string;
          step_number: number; step_type: string; description: string;
          duration_ms: number; timestamp: string
        }) => {
          addAgentStep({
            id:         String(s.id),
            agent:      s.agent_name,
            taskId:     s.task_id,
            stepNumber: s.step_number,
            stepType:   s.step_type,
            description: s.description ?? '',
            durationMs:  s.duration_ms ?? 0,
            timestamp:   s.timestamp,
          })
        })
      })
      .catch(() => {})
  }, [selectedAgent, steps.length, addAgentStep])

  const isVisible = !!selectedAgent

  return (
    <div className={`ad-panel${isVisible ? ' visible' : ''}`}>

      {/* ── Left: steps log ── */}
      <div className="ad-left">

        {/* header */}
        <div className="ad-header">
          <span className={agent?.status === 'running' ? 'status-dot status-dot--running' : 'status-dot status-dot--off'} />
          <span className="ad-name">
            {selectedAgent ? selectedAgent.charAt(0).toUpperCase() + selectedAgent.slice(1) : ''}
          </span>
          <button className="ad-close" onClick={() => setSelectedAgent(null)}>✕</button>
        </div>

        {/* current task */}
        {agent?.lastTask && (
          <div className="ad-task">→ {agent.lastTask}</div>
        )}

        {/* section label */}
        <div className="ad-section-lbl">
          Reasoning live — {steps.length} step
        </div>

        {/* empty state */}
        {steps.length === 0 && (
          <p className="ad-empty">
            Nessuna attività registrata.<br />
            Gli step appariranno in tempo reale.
          </p>
        )}

        {/* step list */}
        {reversedSteps.map((step, i) => {
          const isLatest = i === 0
          const tc = stepTypeClass(step.stepType)
          return (
            <div
              key={step.id}
              className={`ad-step${isLatest ? ' latest' : ''} animate-fade-slide-in`}
              style={{ animationDelay: `${i * 0.03}s` }}
            >
              <div className="ad-step-row">
                <span className={`ad-step-tag ${tc}`}>{STEP_TAG_LABEL[tc]}</span>
                <span className="ad-step-num">step {step.stepNumber}</span>
                <span className="ad-step-desc">{step.description}</span>
              </div>
              <div className="ad-step-meta">
                <span className="ad-step-dur">
                  {step.durationMs > 0 ? `${step.durationMs}ms` : '—'}
                </span>
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Right: metrics sidebar ── */}
      <div className="ad-right">
        <div>
          <div className="ad-metrics-lbl">Metriche</div>
          {[
            {
              label: 'Status',
              value: agent?.status?.toUpperCase() ?? 'IDLE',
              accent: agent?.status === 'running',
            },
            { label: 'Step totali', value: String(steps.length), accent: false },
          ].map((row) => (
            <div key={row.label} className="ad-metric-row">
              <span className="ad-metric-lbl">{row.label}</span>
              <span className={`ad-metric-val${row.accent ? ' accent' : ''}`}>{row.value}</span>
            </div>
          ))}
        </div>

        {/* breakdown per tipo — always shown, empty state when no steps */}
        {(() => {
          if (steps.length === 0) {
            return (
              <div style={{ marginTop: 20 }}>
                <div className="ad-metrics-lbl">Breakdown</div>
                <p className="ad-empty" style={{ marginTop: 10, fontSize: 12 }}>
                  {fetchedRef.current.has(selectedAgent ?? '')
                    ? 'Nessun dato — agente non ancora attivo.'
                    : 'Caricamento…'}
                </p>
              </div>
            )
          }
          const counts = { tool: 0, llm: 0, think: 0 }
          let totalMs = 0
          for (const s of steps) {
            const t = stepTypeClass(s.stepType)
            counts[t]++
            totalMs += s.durationMs ?? 0
          }
          const avgMs = Math.round(totalMs / steps.length)
          const fmtMs = (ms: number) => ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
          const rows = [
            { label: 'Tool',       value: String(counts.tool),  color: 'var(--acc)' },
            { label: 'LLM',        value: String(counts.llm),   color: 'var(--wrn)' },
            { label: 'Think',      value: String(counts.think),  color: '#7eb8ff'    },
            { label: 'Durata tot', value: fmtMs(totalMs),        color: 'var(--tm)'  },
            { label: 'Media step', value: fmtMs(avgMs),          color: 'var(--tm)'  },
          ]
          return (
            <div style={{ marginTop: 20 }}>
              <div className="ad-metrics-lbl">Breakdown</div>
              {rows.map((r) => (
                <div key={r.label} className="ad-metric-row">
                  <span className="ad-metric-lbl">{r.label}</span>
                  <span className="ad-metric-val" style={{ color: r.color }}>{r.value}</span>
                </div>
              ))}
            </div>
          )
        })()}
      </div>

    </div>
  )
}
