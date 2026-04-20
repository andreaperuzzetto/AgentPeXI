import { useStore } from '../../store'

/* ── agent lists (6-slot grid each) ─────────────────────────── */
const PERSONAL_AGENTS = ['recall', 'remind', 'summarize', 'research_personal', 'watcher', 'gmail']
const ETSY_AGENTS     = ['research', 'design', 'publisher', 'analytics', 'finance']
const GRID_SLOTS      = 6

/* ── AgentGrid ───────────────────────────────────────────────── */
function AgentGrid({ agentList }: { agentList: string[] }) {
  const agents = useStore((s) => s.agents)

  // Always exactly GRID_SLOTS entries, padded with null
  const slots: (string | null)[] = [
    ...agentList.slice(0, GRID_SLOTS),
    ...Array(Math.max(0, GRID_SLOTS - agentList.length)).fill(null),
  ]

  return (
    <div className="dc-grid">
      {slots.map((name, i) => {
        if (!name) {
          return (
            <div key={i} className="dc-agent" style={{ visibility: 'hidden' }}>
              <span className="dc-adot" />
              <span className="dc-aname">·</span>
            </div>
          )
        }
        const agent = agents[name]
        const run   = agent?.status === 'running'
        const err   = agent?.status === 'error'
        return (
          <div key={name} className="dc-agent">
            <span className={`dc-adot${run ? ' run' : err ? ' err' : ''}`} />
            <span className="dc-aname">{name.replace('_', ' ')}</span>
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

  const costStr = (n: number) =>
    n === 0 ? '€0' : n < 0.01 ? `€${n.toFixed(4)}` : `€${n.toFixed(3)}`

  const personalCost = costStr(llm.runCost)
  const etsyAgentCount = ETSY_AGENTS.length

  return (
    <>
      {/* ── Personal Layer ── */}
      <div className="dcard" onClick={() => setOverlaySystem('personal')}>
        <div className="dc-head">
          <div className="dc-icon">PSN</div>
          <div>
            <div className="dc-title">Personale </div>
            <div className="dc-sub">Ollama locale · {personalCost}</div>
          </div>
          <span className="dc-badge">LOCALE</span>
        </div>
        <AgentGrid agentList={PERSONAL_AGENTS} />
        <div className="dc-cta">Agenti e servizi personal →</div>
      </div>

      {/* ── Etsy Store ── */}
      <div className="dcard" onClick={() => setOverlaySystem('etsy_store')}>
        <div className="dc-head">
          <div className="dc-icon">ETY</div>
          <div>
            <div className="dc-title">Etsy Store</div>
            <div className="dc-sub">{etsyAgentCount} agenti nel sistema</div>
          </div>
          <span className="dc-badge">PENDING APPROVAL</span>
        </div>
        <AgentGrid agentList={ETSY_AGENTS} />
        <div className="dc-cta">Dettaglio agenti e reasoning →</div>
      </div>
    </>
  )
}
