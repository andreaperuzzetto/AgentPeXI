import { Header } from './components/Header'
import { ChatPanel } from './components/Chat/ChatPanel'
import { AgentMonitor } from './components/AgentMonitor/AgentMonitor'
import { AnalyticsPanel } from './components/Analytics/AnalyticsPanel'
import { ListingsPanel } from './components/Listings/ListingsPanel'
import { ToolFeed } from './components/ToolFeed/ToolFeed'
import { SchedulerPanel } from './components/Scheduler/SchedulerPanel'
import { CostPanel } from './components/CostBreakdown/CostPanel'
import { useWebSocket } from './hooks/useWebSocket'

export default function App() {
  const { send } = useWebSocket()

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateRows: '48px 1fr',
        height: '100vh',
        width: '100vw',
        overflow: 'hidden',
      }}
    >
      <Header />

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '280px 1fr 260px',
          minHeight: 0,
        }}
      >
        {/* Colonna sinistra: Chat */}
        <ChatPanel onSend={send} />

        {/* Colonna centrale */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            overflowY: 'auto',
            borderRight: '1px solid var(--border-strong)',
          }}
        >
          <div style={{ borderBottom: '1px solid var(--border-subtle)' }}>
            <AgentMonitor />
          </div>
          <div style={{ borderBottom: '1px solid var(--border-subtle)' }}>
            <AnalyticsPanel />
          </div>
          <div>
            <ListingsPanel />
          </div>
        </div>

        {/* Colonna destra */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            minHeight: 0,
            background: 'var(--bg-surface-1)',
          }}
        >
          <ToolFeed />
          <div style={{ borderTop: '1px solid var(--border-subtle)' }}>
            <SchedulerPanel />
          </div>
          <div style={{ borderTop: '1px solid var(--border-subtle)', flex: 1, minHeight: 0 }}>
            <CostPanel />
          </div>
        </div>
      </div>
    </div>
  )
}
