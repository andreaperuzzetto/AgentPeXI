import { useState, useEffect, Component, type ReactNode, type ErrorInfo } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { Header } from './components/Header'
import { ReasoningPanel } from './components/ReasoningPanel/ReasoningPanel'
import { ListingsPanel } from './components/Listings/ListingsPanel'
import { DomainCard } from './components/DomainCard/DomainCard'
import { SchedulerPanel } from './components/Scheduler/SchedulerPanel'
import { CostPanel } from './components/CostBreakdown/CostPanel'
import { AnalyticsMiniPanel } from './components/AnalyticsMini/AnalyticsMiniPanel'
import { AnalyticsOverlay } from './components/AnalyticsOverlay/AnalyticsOverlay'
import { SystemOverlay } from './components/SystemOverlay/SystemOverlay'
import { TaskDetailOverlay } from './components/TaskDetail/TaskDetailOverlay'
import { ToolFeed } from './components/ToolFeed/ToolFeed'
import { PepeOrb } from './components/PepeOrb/PepeOrb'
import { PersonalPanel } from './components/PersonalPanel/PersonalPanel'
import { useWebSocket } from './hooks/useWebSocket'
import { useResizableColumn } from './hooks/useResizableColumn'
import { useStore } from './store'

const RIGHT_WIDTH_ETSY     = 340
const RIGHT_WIDTH_PERSONAL = 480

interface ErrState { error: Error | null }
class ErrorBoundary extends Component<{ children: ReactNode }, ErrState> {
  state: ErrState = { error: null }
  static getDerivedStateFromError(error: Error): ErrState { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('[ErrorBoundary]', error, info) }
  render() {
    if (this.state.error) {
      return (
        <div style={{ color: 'var(--danger, #f87171)', padding: 32, fontFamily: 'monospace' }}>
          <strong>Errore critico</strong>
          <pre style={{ marginTop: 8, fontSize: 12 }}>{this.state.error.message}</pre>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  const [analyticsOpen, setAnalyticsOpen] = useState(false)
  const [sistemiTab, setSistemiTab] = useState<'dominio' | 'tool'>('dominio')
  const { setCostsData, addAgentStep, setAnalyticsSummary, setChromaStats, activeDomain } = useStore(
    useShallow((s) => ({
      setCostsData:       s.setCostsData,
      addAgentStep:       s.addAgentStep,
      setAnalyticsSummary: s.setAnalyticsSummary,
      setChromaStats:     s.setChromaStats,
      activeDomain:       s.activeDomain,
    }))
  )
  useWebSocket()

  const { width: rightWidth, transitioning, onHandleMouseDown, snapTo } =
    useResizableColumn(RIGHT_WIDTH_PERSONAL)

  // Snap animato alla larghezza suggerita ad ogni cambio dominio
  useEffect(() => {
    snapTo(activeDomain === 'personal' ? RIGHT_WIDTH_PERSONAL : RIGHT_WIDTH_ETSY)
  }, [activeDomain, snapTo])

  /* ── Hydrate agent steps on mount (eager, before WS connects — fixes ReasoningPanel after refresh) ── */
  useEffect(() => {
    fetch('/api/agents/steps/recent?limit=50')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!Array.isArray(data?.steps) || data.steps.length === 0) return
        data.steps.forEach((s: {
          id: number; task_id: string; agent_name: string;
          step_number: number; step_type: string; description: string;
          duration_ms: number; timestamp: string
        }) => {
          addAgentStep({
            id: String(s.id),
            agent: s.agent_name,
            taskId: s.task_id,
            stepNumber: s.step_number,
            stepType: s.step_type,
            description: s.description ?? '',
            durationMs: s.duration_ms ?? 0,
            timestamp: s.timestamp,
          })
        })
      })
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ── Fetch costs / analytics / chroma — on mount + ogni 30 s ── */
  useEffect(() => {
    const fetchCosts = () =>
      fetch('/api/costs?days=30')
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          if (!data?.breakdown) return
          const b = data.breakdown
          const budgetUsd = b.budget_threshold_eur ? b.budget_threshold_eur / 0.92 : undefined
          // runCost NON viene settato dal REST — si accumula solo dagli eventi WS llm_call.
          // In questo modo mostra il costo della sessione corrente, non il totale di oggi.
          setCostsData({
            total:    b.total    ?? 0,
            perAgent: b.per_agent ?? {},
            perDay:   b.per_day   ?? {},
            budgetMonthlyUsd: budgetUsd,
          })
        })
        .catch(() => {})

    const fetchAnalytics = () =>
      fetch('/api/analytics/summary?days=14')
        .then((r) => r.ok ? r.json() : null)
        .then((data) => { if (data?.summary) setAnalyticsSummary(data.summary) })
        .catch(() => {})

    const fetchChroma = () =>
      fetch('/api/memory/stats')
        .then((r) => r.ok ? r.json() : null)
        .then((data) => { if (data?.chroma) setChromaStats(data.chroma) })
        .catch(() => {})

    fetchCosts(); fetchAnalytics(); fetchChroma()
    const id = setInterval(() => { fetchCosts(); fetchAnalytics(); fetchChroma() }, 30_000)
    return () => clearInterval(id)
  }, [setCostsData, setAnalyticsSummary, setChromaStats])

  return (
    <ErrorBoundary>
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--base)' }}>
      <Header />

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* ── Center column ── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>

          {/* Pepe — top 50%: Orb in Personal, ReasoningPanel in Etsy */}
          <div style={{
            flex: '0 0 50%',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            borderBottom: '2px solid var(--b1)',
          }}>
            {activeDomain === 'personal' ? <PepeOrb /> : <ReasoningPanel />}
          </div>

          {/* Sistemi — bottom 50% */}
          <div style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            minHeight: 0,
          }}>
            {/* header */}
            <div className="panel-header">
              <span className="section-label">Sistemi</span>
            </div>
            {/* tab bar */}
            <div style={{ display: 'flex', borderBottom: '1px solid var(--b0)', flexShrink: 0 }}>
              {(['dominio', 'tool'] as const).map(tab => (
                <button key={tab}
                  onClick={() => setSistemiTab(tab)}
                  style={{
                    flex: 1, height: 32, background: 'none', border: 'none', cursor: 'pointer',
                    fontFamily: 'var(--fd)', fontSize: 12, letterSpacing: '0.05em',
                    color: sistemiTab === tab ? 'var(--accent)' : 'var(--tf)',
                    borderBottom: sistemiTab === tab ? '2px solid var(--accent)' : '2px solid transparent',
                    transition: 'color .2s var(--e-io), border-color .2s var(--e-io)',
                    textTransform: 'uppercase',
                  }}
                >
                  {tab === 'dominio' ? 'Dominio' : 'Tool Activity'}
                </button>
              ))}
            </div>
            {/* tab content */}
            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
              {sistemiTab === 'dominio' ? (
                <div style={{ flex: 1, overflowY: 'auto', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <DomainCard />
                </div>
              ) : (
                <ToolFeed />
              )}
            </div>
          </div>
        </div>

        {/* ── Resize handle ── */}
        <div
          onMouseDown={onHandleMouseDown}
          style={{
            width: 4,
            flexShrink: 0,
            cursor: 'col-resize',
            background: 'var(--b0)',
            transition: 'background .2s var(--e-io)',
            zIndex: 10,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(45,232,106,0.35)')}
          onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--b0)')}
        />

        {/* ── Right column ── */}
        <div
          style={{
            width: rightWidth,
            flexShrink: 0,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            transition: transitioning ? 'width 0.4s var(--e-out)' : 'none',
          }}
        >
          {activeDomain === 'personal' ? (
            /* ── Personal right column ── */
            <PersonalPanel />
          ) : (
            /* ── Etsy right column ── */
            <>
              {/* Listing — flex:3 */}
              <div style={{ flex: 3, overflow: 'hidden', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <ListingsPanel />
              </div>

              {/* Bottom-right — altezza naturale, listing si adatta */}
              <div style={{
                flexShrink: 0,
                borderTop: '1px solid var(--b0)',
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
              }}>
                {/* Scheduler */}
                <div style={{ flexShrink: 0, borderBottom: '1px solid var(--b0)' }}>
                  <SchedulerPanel />
                </div>
                {/* Costo */}
                <div style={{ flexShrink: 0, borderBottom: '1px solid var(--b0)' }}>
                  <CostPanel />
                </div>
                {/* Analytics mini */}
                <div style={{ flexShrink: 0 }}>
                  <AnalyticsMiniPanel onOpen={() => setAnalyticsOpen(true)} />
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* System overlay modal */}
      <SystemOverlay />
      {/* Analytics overlay modal */}
      <AnalyticsOverlay open={analyticsOpen} onClose={() => setAnalyticsOpen(false)} />
      {/* Task detail overlay */}
      <TaskDetailOverlay />
    </div>
    </ErrorBoundary>
  )
}
