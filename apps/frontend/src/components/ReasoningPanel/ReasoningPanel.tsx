import { useStore } from '../../store'
import './ReasoningPanel.css'

const FLOW_AGENTS = ['research', 'design', 'publisher', 'analytics'] as const

function flowState(status: string): 'run' | 'done' | 'wait' {
  if (status === 'running') return 'run'
  if (status === 'done')    return 'done'
  return 'wait'
}

const SVG_RUN = (
  <svg viewBox="0 0 16 16" fill="none">
    <circle cx="8" cy="8" r="3" fill="currentColor" opacity=".9"/>
    <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="1" opacity=".3"/>
  </svg>
)
const SVG_WAIT = (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
    <circle cx="8" cy="8" r="5.5"/>
    <path d="M8 5v3.5l2 1.5"/>
  </svg>
)
const SVG_DONE = (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3.5 8.5l3 3 6-6"/>
  </svg>
)
const SVG_PUB = (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M8 10V4M5 7l3-3 3 3"/><path d="M4 12h8"/>
  </svg>
)

function nodeSvg(agent: string, state: 'run' | 'done' | 'wait') {
  if (state === 'run')  return SVG_RUN
  if (state === 'done') return SVG_DONE
  if (agent === 'publisher') return SVG_PUB
  return SVG_WAIT
}

function fmtTok(n: number): string {
  if (n === 0) return '0'
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

export function ReasoningPanel() {
  const agents      = useStore((s) => s.agents)
  const allSteps    = useStore((s) => s.agentSteps)
  const setSelectedTaskId = useStore((s) => s.setSelectedTaskId)
  const sysStatus   = useStore((s) => s.systemStatus)
  const llmStats    = useStore((s) => s.llmStats)
  const contextState = useStore((s) => s.contextState)
  const chromaStats  = useStore((s) => s.chromaStats)
  const connectedAt  = useStore((s) => s.connectedAt)
  const wsConnected  = useStore((s) => s.wsConnected)

  const activeCount  = FLOW_AGENTS.filter((n) => agents[n]?.status === 'running').length
  const runningAgent = FLOW_AGENTS.find((n) => agents[n]?.status === 'running') ?? 'research'
  const runningSteps = allSteps[runningAgent] ?? []

  const pipelineSteps = runningSteps.length > 0
    ? runningSteps.slice(-4).map((s) => ({
        tag: s.stepType.slice(0, 4).toUpperCase(),
        desc: s.description,
        dur: s.durationMs > 0 ? `${s.durationMs}ms` : '—',
        taskId: s.taskId,
      }))
    : []

  const queueRows = FLOW_AGENTS
    .map((n) => ({ name: n, status: agents[n]?.status ?? 'idle', task: agents[n]?.lastTask || '—' }))
    .slice(0, 4)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

      {/* Header */}
      <div style={{
        height: 38, padding: '0 14px', background: 'var(--s1)',
        borderBottom: '1px solid var(--b0)', flexShrink: 0,
        display: 'flex', alignItems: 'center', gap: 9,
      }}>
        <span className={`status-dot ${activeCount > 0 ? 'status-dot--running' : 'status-dot--off'}`} />
        <span style={{ fontFamily: 'var(--fh)', fontSize: 13, fontWeight: 800, letterSpacing: '0.04em', color: 'var(--accent)' }}>
          Pepe
        </span>
        {activeCount > 0 && <span className="badge-pill badge-pill--run">RUNNING</span>}
        <span style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tm)', marginLeft: 'auto' }}>
          Orchestratore{activeCount > 0 ? ` · ${activeCount} attivi` : ''}
        </span>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 13px', display: 'flex', flexDirection: 'column', gap: 10 }}>

        {/* ① Pipeline attiva */}
        {wsConnected ? (
        <div className="card card--active" style={{ padding: '13px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span className="status-dot status-dot--running" />
            <span style={{ fontFamily: 'var(--fh)', fontSize: 12, fontWeight: 700, letterSpacing: '0.04em', color: 'var(--tp)', flex: 1 }}>
              Pipeline attiva
            </span>
            <span style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tm)' }}>
              {(() => {
                if (!connectedAt) return '—'
                const diff = Date.now() - connectedAt
                const mins = Math.floor(diff / 60000)
                const hrs = Math.floor(mins / 60)
                if (hrs > 0) return `${hrs}h ${mins % 60}m`
                return `${mins}m`
              })()}
            </span>
          </div>
          <div style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tm)', marginTop: 6 }}>
            → {agents[runningAgent]?.lastTask || 'In attesa task…'}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginTop: 9 }}>
            {pipelineSteps.length > 0 ? pipelineSteps.map((step, i) => {
              const isLatest = i === pipelineSteps.length - 1
              return (
                <div key={i} className={`pstep${isLatest ? ' latest' : ''}`}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setSelectedTaskId(step.taskId)}
                >
                  <span className="ptag">{step.tag}</span>
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: isLatest ? 'var(--tp)' : 'var(--tm)' }}>
                    {step.desc}
                  </span>
                  <span style={{ color: 'var(--tf)', fontSize: 11, flexShrink: 0 }}>{step.dur}</span>
                </div>
              )
            }) : (
              <div style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tf)', padding: '4px 0' }}>
                Nessun step attivo
              </div>
            )}
          </div>
          <div className="pbar">
            <div style={{ height: '100%', borderRadius: 99, background: 'linear-gradient(90deg,var(--accent),rgba(45,232,106,.25))', animation: 'pgrow 28s linear forwards' }} />
          </div>
        </div>
        ) : (
        <div className="card" style={{ padding: '13px 14px', opacity: 0.5 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <span className="status-dot status-dot--off" />
            <span style={{ fontFamily: 'var(--fh)', fontSize: 12, fontWeight: 700, letterSpacing: '0.04em', color: 'var(--tm)', flex: 1 }}>
              Pipeline offline
            </span>
          </div>
          <div style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)', marginTop: 6 }}>
            Sistema disconnesso — in attesa di connessione
          </div>
        </div>
        )}

        {/* ② Flusso pipeline */}
        <div className="card" style={{ padding: '12px 14px' }}>
          <div style={{ fontFamily: 'var(--fh)', fontSize: 9, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--tm)', marginBottom: 14 }}>
            Flusso pipeline
          </div>
          <div className="flow-row">
            {FLOW_AGENTS.map((name, i) => {
              const state = flowState(agents[name]?.status ?? 'idle')
              const isLast = i === FLOW_AGENTS.length - 1
              const prevName = FLOW_AGENTS[i - 1]
              const prevState = i > 0
                ? flowState(agents[prevName]?.status ?? 'idle')
                : 'wait'
              const connDone = prevState === 'done'
              return (
                <div key={name} style={{ display: 'contents' }}>
                  <div className="flow-node">
                    <div className={`flow-node-dot ${state}`}>
                      {nodeSvg(name, state)}
                    </div>
                    <span className={`flow-label${state !== 'wait' ? ` ${state}` : ''}`}>
                      {name.charAt(0).toUpperCase() + name.slice(1)}
                    </span>
                  </div>
                  {!isLast && <div className={`flow-connector${connDone ? ' done' : ''}`} />}
                </div>
              )
            })}
          </div>
        </div>

        {/* ③ Token & Costo */}
        <div className="card" style={{ padding: '12px 14px' }}>
          <div style={{ fontFamily: 'var(--fh)', fontSize: 9, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--tm)', marginBottom: 9 }}>
            Token &amp; Costo — pipeline corrente
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
            {[
              { lbl: 'Input tok',  val: fmtTok(llmStats.inputTokens)  || '—', sub: '↑ agenti',    accent: false },
              { lbl: 'Output tok', val: fmtTok(llmStats.outputTokens) || '—', sub: 'questa run',  accent: false },
              { lbl: 'Costo',      val: `$${llmStats.runCost.toFixed(3)}`, sub: 'questa run', accent: true },
            ].map((item) => (
              <div key={item.lbl} className="tok-item">
                <div style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)', letterSpacing: '0.03em', textTransform: 'uppercase' }}>{item.lbl}</div>
                <div style={{ fontFamily: 'var(--fd)', fontSize: 14, color: item.accent ? 'var(--accent)' : 'var(--tp)', marginTop: 3, fontWeight: 500 }}>{item.val}</div>
                <div style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)', marginTop: 1 }}>{item.sub}</div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 10 }}>
            {(() => {
              const CTX_MAX = 128000
              const ctxPct = llmStats.inputTokens > 0
                ? Math.min(Math.round((llmStats.inputTokens / CTX_MAX) * 100), 100)
                : 0
              const ctxLabel = llmStats.inputTokens > 0
                ? `${fmtTok(llmStats.inputTokens)} / 128k`
                : '— / 128k'
              const chromaCount = chromaStats?.count ?? 0
              const chromaMax = 200
              const chromaPct = chromaCount > 0 ? Math.min(Math.round((chromaCount / chromaMax) * 100), 100) : 0
              const chromaLabel = chromaStats ? `${chromaCount} / ${chromaMax}` : '— / 200'
              return [
                { label: 'Context window',           right: ctxLabel,    rColor: 'var(--tm)',   pct: ctxPct, grad: 'linear-gradient(90deg,var(--accent),rgba(45,232,106,.4))' },
                { label: 'ChromaDB chunks caricati', right: chromaLabel, rColor: chromaCount > 0 ? 'var(--warn)' : 'var(--tf)', pct: chromaPct, grad: 'linear-gradient(90deg,var(--warn),rgba(240,180,41,.35))' },
              ].map((bar, bi) => (
                <div key={bar.label} style={{ marginBottom: bi === 0 ? 8 : 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
                    <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)', flex: 1 }}>{bar.label}</span>
                    <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: bar.rColor }}>{bar.right}</span>
                  </div>
                  <div style={{ height: 3, background: 'var(--b0)', borderRadius: 99, overflow: 'hidden' }}>
                    <div style={{ width: `${bar.pct}%`, height: '100%', borderRadius: 99, background: bar.grad, transition: 'width .8s var(--e-out)' }} />
                  </div>
                </div>
              ))
            })()}
          </div>
        </div>

        {/* ④ Contesto decisionale */}
        <div className="card" style={{ padding: '12px 14px' }}>
          <div style={{ fontFamily: 'var(--fh)', fontSize: 9, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--tm)', marginBottom: 8 }}>
            Contesto decisionale
          </div>
          {(() => {
            const ctx = contextState
            const rows = ctx ? [
              { l: 'Confidence gate',           v: `${Math.round((ctx.confidence_threshold ?? 0.85) * 100)}% soglia`, vc: 'var(--ok)' },
              { l: 'Strategia attiva',           v: ctx.strategy ?? '—',           vc: 'var(--accent)' },
              { l: 'Domain attivo',              v: ctx.domain ?? '—',             vc: 'var(--accent)' },
              { l: 'Failure recenti (ChromaDB)', v: `${ctx.failure_count ?? 0} pattern`, vc: ctx.failure_count ? 'var(--warn)' : 'var(--tm)' },
              { l: 'Prossima azione',            v: ctx.next_action ?? '—',        vc: 'var(--tm)' },
              { l: 'Retry policy',               v: ctx.retry_policy ?? '—',       vc: 'var(--tm)' },
            ] : [
              { l: 'Confidence gate',           v: '—',                    vc: 'var(--tf)' },
              { l: 'Strategia attiva',           v: '—',                    vc: 'var(--tf)' },
              { l: 'Domain attivo',              v: '—',                    vc: 'var(--tf)' },
              { l: 'Failure recenti (ChromaDB)', v: '—',                    vc: 'var(--tf)' },
              { l: 'Prossima azione',            v: '—',                    vc: 'var(--tf)' },
              { l: 'Retry policy',               v: '—',                    vc: 'var(--tf)' },
            ]
            return rows.map((row, i) => (
              <div key={i} className="ctx-row">
                <span style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tm)' }}>{row.l}</span>
                <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: row.vc }}>{row.v}</span>
              </div>
            ))
          })()}
        </div>

        {/* ⑤ Coda task */}
        <div className="card" style={{ padding: '12px 14px' }}>
          <div style={{ fontFamily: 'var(--fh)', fontSize: 9, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--tm)', marginBottom: 9 }}>
            Coda task
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {queueRows.map((row) => {
              const isRun = row.status === 'running'
              return (
                <div key={row.name} style={{
                  display: 'flex', alignItems: 'center', gap: 9,
                  padding: '6px 9px', borderRadius: 6,
                  background: isRun ? 'rgba(45,232,106,.06)' : 'var(--s2)',
                  border: `1px solid ${isRun ? 'rgba(45,232,106,.12)' : 'var(--b0)'}`,
                  cursor: 'pointer',
                }}
                onClick={() => {
                  const steps = allSteps[row.name]
                  const last = steps?.[steps.length - 1]
                  if (last) setSelectedTaskId(last.taskId)
                }}
                >
                  <span className={`status-dot ${isRun ? 'status-dot--running' : 'status-dot--off'}`} />
                  <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: isRun ? 'var(--tp)' : 'var(--tm)', flex: 1 }}>
                    {row.name} · {row.task}
                  </span>
                  <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: isRun ? 'var(--accent)' : 'var(--tf)' }}>
                    {isRun ? 'RUNNING' : 'QUEUED'}
                  </span>
                </div>
              )
            })}
          </div>
        </div>

      </div>
    </div>
  )
}
