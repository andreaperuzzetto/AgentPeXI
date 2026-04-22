import { useMemo } from 'react'
import { useStore } from '../../store'
import type { AgentStep } from '../../types'

/* ── fallback statici ────────────────────────────────────────── */
const FALLBACK_PERSONAL = ['recall', 'remind', 'summarize', 'research_personal', 'watcher', 'gmail']
const FALLBACK_ETSY     = ['research', 'design', 'publisher', 'analytics', 'finance']

/* agenti che sono servizi, non LLM agent — esclusi dal conteggio */
const SERVICES = new Set(['watcher'])

/* ── helpers ─────────────────────────────────────────────────── */
const costStr = (n: number) =>
  n === 0 ? '€0' : n < 0.01 ? `€${n.toFixed(4)}` : `€${n.toFixed(3)}`

function domainCost(perAgent: Record<string, number>, agentList: string[]): number {
  return agentList.reduce((sum, name) => sum + (perAgent[name] ?? 0), 0)
}

function tagType(stepType: string): 'tool' | 'llm' | 'think' {
  if (stepType === 'tool' || stepType === 'tool_call') return 'tool'
  if (stepType === 'llm'  || stepType === 'llm_call')  return 'llm'
  return 'think'
}

function fmtTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
}

function stepsToday(steps: AgentStep[]): number {
  const today = new Date().toISOString().slice(0, 10)
  return steps.filter((s) => s.timestamp.slice(0, 10) === today).length
}

/* ── HeroBox — ultimo evento del dominio ─────────────────────── */
function HeroBox({ agentList }: { agentList: string[] }) {
  const agentSteps = useStore((s) => s.agentSteps)

  const latest = useMemo<AgentStep | null>(() => {
    const all = agentList.flatMap((name) => agentSteps[name] ?? [])
    if (all.length === 0) return null
    return all.reduce((a, b) =>
      new Date(a.timestamp) > new Date(b.timestamp) ? a : b
    )
  }, [agentList, agentSteps])

  if (!latest) {
    return (
      <div className="dc-hero">
        <div className="dc-hero-row">
          <span className="dc-hero-tag idle">IDLE</span>
        </div>
        <div className="dc-hero-idle">Nessuna attività recente</div>
      </div>
    )
  }

  const tag = tagType(latest.stepType)
  return (
    <div className="dc-hero">
      <div className="dc-hero-row">
        <span className={`dc-hero-tag ${tag}`}>{tag.toUpperCase()}</span>
        <span className="dc-hero-agent">{latest.agent.replace('_', ' ')}</span>
        <span className="dc-hero-time">{fmtTime(latest.timestamp)}</span>
      </div>
      <div className="dc-hero-desc">{latest.description}</div>
    </div>
  )
}

/* ── MetricStrip — 3 valori aggregati ───────────────────────── */
function MetricStrip({
  agentList,
  cost,
}: {
  agentList: string[]
  cost: string
}) {
  const agents     = useStore((s) => s.agents)
  const agentSteps = useStore((s) => s.agentSteps)

  const running = agentList.filter(
    (name) => !SERVICES.has(name) && agents[name]?.status === 'running'
  ).length

  const todayCount = useMemo(() => {
    const all = agentList.flatMap((name) => agentSteps[name] ?? [])
    return stepsToday(all)
  }, [agentList, agentSteps])

  const metrics = [
    { val: String(running),    lbl: 'Running',    accent: running > 0 },
    { val: String(todayCount), lbl: 'Step oggi',  accent: false },
    { val: cost,               lbl: 'Costo',      accent: false },
  ]

  return (
    <div className="dc-strip">
      {metrics.map((m) => (
        <div key={m.lbl} className="dc-metric">
          <span className={`dc-metric-val${m.accent ? ' accent' : ''}`}>{m.val}</span>
          <span className="dc-metric-lbl">{m.lbl}</span>
        </div>
      ))}
    </div>
  )
}

/* ── AgentsGrid — chip compatti ─────────────────────────────── */
function AgentsGrid({ agentList }: { agentList: string[] }) {
  const agents = useStore((s) => s.agents)

  return (
    <div className="dc-agents-grid">
      {agentList.map((name) => {
        const agent = agents[name]
        const run   = agent?.status === 'running'
        const err   = agent?.status === 'error'
        const isSvc = SERVICES.has(name)
        return (
          <div key={name} className="dc-agent-chip">
            <span className={`dc-adot${run ? ' run' : err ? ' err' : ''}`} />
            <span className="dc-aname">{name.replace('_', ' ')}</span>
            {isSvc && <span className="dc-svc-lbl">svc</span>}
          </div>
        )
      })}
    </div>
  )
}

/* ── DomainCard ──────────────────────────────────────────────── */
export function DomainCard() {
  const setOverlaySystem = useStore((s) => s.setOverlaySystem)
  const llm              = useStore((s) => s.llmStats)
  const domainConfig     = useStore((s) => s.domainConfig)

  const personalAgents = domainConfig?.personal.agents ?? FALLBACK_PERSONAL
  const etsyAgents     = domainConfig?.etsy.agents     ?? FALLBACK_ETSY

  const personalAgentCount = personalAgents.filter((n) => !SERVICES.has(n)).length
  const personalSvcCount   = personalAgents.filter((n) =>  SERVICES.has(n)).length
  const etsyAgentCount     = etsyAgents.filter((n) => !SERVICES.has(n)).length
  const etsySvcCount       = etsyAgents.filter((n) =>  SERVICES.has(n)).length

  const personalCost = costStr(domainCost(llm.perAgent, personalAgents))
  const etsyCost     = costStr(domainCost(llm.perAgent, etsyAgents))

  return (
    <>
      {/* ── Personal Layer ── */}
      <div className="dcard" data-zone="personal" onClick={() => setOverlaySystem('personal')}>
        <div className="dc-head">
          <div className="dc-icon">PSN</div>
          <div>
            <div className="dc-title">Personale</div>
            <div className="dc-sub">
              {personalAgentCount} agenti{personalSvcCount > 0 ? ` · ${personalSvcCount} servizio` : ''}
            </div>
          </div>
          <span className="dc-badge">CLAUDE</span>
        </div>
        <HeroBox agentList={personalAgents} />
        <MetricStrip agentList={personalAgents} cost={personalCost} />
        <AgentsGrid agentList={personalAgents} />
        <div className="dc-cta">Agenti e servizi personal →</div>
      </div>

      {/* ── Etsy Store ── */}
      <div className="dcard" data-zone="etsy" onClick={() => setOverlaySystem('etsy_store')}>
        <div className="dc-head">
          <div className="dc-icon">ETY</div>
          <div>
            <div className="dc-title">Etsy Store</div>
            <div className="dc-sub">
              {etsyAgentCount} agenti{etsySvcCount > 0 ? ` · ${etsySvcCount} servizio` : ''}
            </div>
          </div>
          <span className="dc-badge">PENDING APPROVAL</span>
        </div>
        <HeroBox agentList={etsyAgents} />
        <MetricStrip agentList={etsyAgents} cost={etsyCost} />
        <AgentsGrid agentList={etsyAgents} />
        <div className="dc-cta">Dettaglio agenti e reasoning →</div>
      </div>
    </>
  )
}
