import { useStore } from '../../store'

const EMPTY_STEPS: never[] = []

/* Step type → tag label + style class */
const STEP_TAG: Record<string, { label: string; cls: string }> = {
  llm_call:  { label: 'LLM',   cls: 'step-tag step-tag--llm' },
  tool_call: { label: 'TOOL',  cls: 'step-tag step-tag--tool' },
  thinking:  { label: 'THINK', cls: 'step-tag step-tag--think' },
}

interface Props { name: string }

export function AgentReasoningCard({ name }: Props) {
  const agent = useStore((s) => s.agents[name])
  const steps = useStore((s) => s.agentSteps[name] ?? EMPTY_STEPS)

  const isRunning = agent?.status === 'running'
  const isError   = agent?.status === 'error'
  const isActive  = isRunning || isError || steps.length > 0

  /* ── Idle: compact row ── */
  if (!isActive) {
    return (
      <div
        className="card"
        style={{ padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 9 }}
      >
        <span className="status-dot status-dot--off" />
        <span
          style={{
            fontFamily: 'var(--fh)',
            fontSize: 13,
            fontWeight: 700,
            letterSpacing: '0.06em',
            textTransform: 'uppercase' as const,
            color: 'var(--tm)',
            flex: 1,
          }}
        >
          {name}
        </span>
        <span className="badge-pill">IDLE</span>
      </div>
    )
  }

  /* ── Active: full card with steps (.pepe-card) ── */
  const lastSteps = steps.slice(-5)
  const statusColor =
    isRunning ? 'var(--accent)' :
    isError   ? 'var(--err)' :
    'var(--ok)'

  const statusLabel =
    isRunning ? 'RUNNING' :
    isError   ? 'ERROR' :
    'DONE'

  return (
    <div
      className={`card${isRunning ? ' card--active animate-fade-slide-up' : ''}`}
      style={{ padding: '13px 14px' }}
    >
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <span
          className={isRunning ? 'status-dot status-dot--running' : 'status-dot'}
          style={
            isError
              ? { background: 'var(--err)' }
              : !isRunning
              ? { background: 'var(--ok)' }
              : undefined
          }
        />
        <span
          style={{
            fontFamily: 'var(--fh)',
            fontSize: 14,
            fontWeight: 700,
            letterSpacing: '0.04em',
            color: 'var(--tp)',
            flex: 1,
          }}
        >
          {name.charAt(0).toUpperCase() + name.slice(1)}
        </span>
        <span
          className="badge-pill"
          style={{
            color: statusColor,
            borderColor: isRunning
              ? 'rgba(45,232,106,.28)'
              : isError
              ? 'rgba(224,82,82,.28)'
              : 'rgba(45,232,106,.2)',
          }}
        >
          {statusLabel}
        </span>
      </div>

      {/* Last task — .pepe-task */}
      {agent?.lastTask && (
        <div
          style={{
            fontFamily: 'var(--fd)',
            fontSize: 13,
            color: 'var(--tm)',
            marginTop: 6,
          }}
        >
          → {agent.lastTask.length > 65 ? agent.lastTask.slice(0, 65) + '…' : agent.lastTask}
        </div>
      )}

      {/* Steps — .pepe-steps / .pstep */}
      {lastSteps.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginTop: 9 }}>
          {lastSteps.map((step, i) => {
            const isLatest = i === lastSteps.length - 1
            const tag = STEP_TAG[step.stepType] ?? { label: step.stepType.slice(0, 4).toUpperCase(), cls: 'step-tag' }
            return (
              <div
                key={step.id}
                className="animate-fade-slide-in"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 7,
                  fontFamily: 'var(--fd)',
                  fontSize: 14,
                  padding: '3px 7px',
                  borderRadius: 4,
                  borderLeft: `2px solid ${isLatest ? 'var(--accent)' : 'transparent'}`,
                  background: isLatest ? 'rgba(45,232,106,.06)' : 'transparent',
                  transition: 'background .25s var(--e-io), border-color .25s var(--e-io)',
                  animationDelay: `${i * 0.03}s`,
                }}
              >
                <span className={tag.cls}>{tag.label}</span>
                <span
                  style={{
                    flex: 1,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap' as const,
                    color: isLatest ? 'var(--tp)' : 'var(--tm)',
                  }}
                >
                  {step.description.length > 72
                    ? step.description.slice(0, 72) + '…'
                    : step.description}
                </span>
                <span style={{ color: 'var(--tf)', fontSize: 13, flexShrink: 0 }}>
                  {step.durationMs > 0 ? `${step.durationMs}ms` : '—'}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* Progress bar — only while running */}
      {isRunning && (
        <div
          style={{
            marginTop: 9,
            height: 2,
            borderRadius: 99,
            background: 'var(--b0)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              height: '100%',
              borderRadius: 99,
              background: 'linear-gradient(90deg, var(--accent), rgba(45,232,106,.25))',
              animation: 'progressGrow 28s linear forwards',
              willChange: 'width',
            }}
          />
        </div>
      )}
    </div>
  )
}
