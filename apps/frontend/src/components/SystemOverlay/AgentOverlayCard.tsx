import { useStore } from '../../store'

const EMPTY_STEPS: never[] = []

const AGENT_DESCS: Record<string, string> = {
  // Etsy
  research:  'Analisi di mercato, ricerca nicchie, trend Etsy e dati competitivi.',
  design:    'Generazione immagini, prompt engineering, output SVG e PNG ad alta risoluzione.',
  publisher: 'Creazione listing, titoli SEO, tag e pubblicazione su Etsy.',
  analytics: 'Monitoraggio KPI, A/B test, analisi performance e reportistica.',
  finance:   'Gestione costi, margini, budget mensile e reportistica finanziaria.',
  // Personal
  recall:    'Ricerca nella memoria schermo. Risponde a "cosa stavo guardando?" via Ollama.',
  remind:    'Gestione promemoria e notifiche programmate.',
  summarize: 'Sintesi automatica di documenti, email e contenuti web.',
  research_personal: 'Ricerca web e analisi documenti per uso personale.',
  watcher:   'Monitoraggio passivo. Cattura schermo, OCR e indicizzazione in ChromaDB.',
  gmail:     'Integrazione Gmail per lettura, risposta e archiviazione email.',
}

const SERVICES = new Set(['watcher'])

interface Props {
  agentName: string
  index: number
}

export function AgentOverlayCard({ agentName, index }: Props) {
  const agent            = useStore((s) => s.agents[agentName])
  const steps            = useStore((s) => s.agentSteps[agentName] ?? EMPTY_STEPS)
  const selectedAgent    = useStore((s) => s.selectedAgent)
  const setSelectedAgent = useStore((s) => s.setSelectedAgent)

  const isSelected = selectedAgent === agentName
  const isRunning  = agent?.status === 'running'
  const isError    = agent?.status === 'error'
  const isService  = SERVICES.has(agentName)

  const badgeClass = isRunning ? 'run' : isError ? 'err' : ''
  const statusLabel = agent?.status?.toUpperCase() ?? 'IDLE'

  return (
    <div
      className={`card ov-card animate-card-up${isSelected ? ' selected' : ''}`}
      style={{ animationDelay: `${index * 0.05}s` }}
      onClick={() => setSelectedAgent(isSelected ? null : agentName)}
    >
      {/* header */}
      <div className="ov-card-header">
        <span
          className={isRunning ? 'status-dot status-dot--running' : 'status-dot'}
          style={
            isError   ? { background: 'var(--err)' } :
            !isRunning ? { background: 'var(--tf)' } :
            undefined
          }
        />
        <div className="ov-card-info">
          <span className="ov-card-name">{agentName.replace('_', ' ')}</span>
          {isService && <span className="ov-card-svc">Servizio</span>}
        </div>
        <span className={`ov-card-badge${badgeClass ? ` ${badgeClass}` : ''}`}>
          {statusLabel}
        </span>
      </div>

      {/* description / last task */}
      <div className="ov-card-desc">
        {agent?.lastTask
          ? (agent.lastTask.length > 80 ? agent.lastTask.slice(0, 80) + '…' : agent.lastTask)
          : (AGENT_DESCS[agentName] ?? '')}
      </div>

      {/* last 3 steps preview */}
      {steps.length > 0 && (
        <div className="ov-card-steps">
          {steps.slice(-3).map((step, i) => {
            const isLatest = i === Math.min(2, steps.length - 1)
            return (
              <div key={step.id} className={`ov-card-step${isLatest ? ' latest' : ''}`}>
                {step.description.length > 52
                  ? step.description.slice(0, 52) + '…'
                  : step.description}
              </div>
            )
          })}
        </div>
      )}

      {/* footer */}
      <div className="ov-card-foot">
        <span className="ov-card-count">
          {isService ? `${steps.length} catture` : `${steps.length} step registrati`}
        </span>
        <span className={`ov-card-cta${isSelected ? ' open' : ''}`}>
          {isSelected ? 'Aperto ✓' : 'Dettaglio →'}
        </span>
      </div>
    </div>
  )
}
