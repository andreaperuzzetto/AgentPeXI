import { useMemo } from 'react'
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
  const agent = useStore((s) => s.agents[selectedAgent ?? ''])
  const steps = useStore((s) => s.agentSteps[selectedAgent ?? ''] ?? EMPTY_STEPS)
  const reversedSteps = useMemo(() => [...steps].reverse(), [steps])

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
            { label: 'Status',      value: agent?.status?.toUpperCase() ?? 'IDLE',   accent: agent?.status === 'running' },
            { label: 'Step totali', value: String(steps.length),                     accent: false },
            { label: 'Ultimo step', value: steps[steps.length - 1]?.stepType ?? '—', accent: false },
          ].map((row) => (
            <div key={row.label} className="ad-metric-row">
              <span className="ad-metric-lbl">{row.label}</span>
              <span className={`ad-metric-val${row.accent ? ' accent' : ''}`}>{row.value}</span>
            </div>
          ))}
        </div>
      </div>

    </div>
  )
}
