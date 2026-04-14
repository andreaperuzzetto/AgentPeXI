import { useStore } from '../../store'
import { AgentCard } from './AgentCard'

export function AgentMonitor() {
  const agents = useStore((s) => s.agents)

  return (
    <section>
      <div style={{ padding: '10px 12px 6px' }}>
        <span className="section-label">Agenti</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {Object.entries(agents).map(([name, state]) => (
          <AgentCard key={name} name={name} state={state} />
        ))}
      </div>
    </section>
  )
}
