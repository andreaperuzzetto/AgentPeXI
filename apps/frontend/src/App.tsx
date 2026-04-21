import { useEffect, useState, Component, type ReactNode, type ErrorInfo } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { Header } from './components/Header'
import { ReasoningPanel } from './components/ReasoningPanel/ReasoningPanel'
import { PepeOrb } from './components/PepeOrb/PepeOrb'
import { PersonalQuickCard } from './components/PersonalQuickCard/PersonalQuickCard'
import { AnalyticsMiniPanel } from './components/AnalyticsMini/AnalyticsMiniPanel'
import { DomainCard } from './components/DomainCard/DomainCard'
import { AnalyticsOverlay } from './components/AnalyticsOverlay/AnalyticsOverlay'
import { SystemOverlay } from './components/SystemOverlay/SystemOverlay'
import { ToolActivityChip } from './components/ToolActivityChip/ToolActivityChip'
import { VoiceNotificationStack } from './components/VoiceNotification/VoiceNotificationStack'
import { useWebSocket } from './hooks/useWebSocket'
import { useStore } from './store'

interface ErrState { error: Error | null }
class ErrorBoundary extends Component<{ children: ReactNode }, ErrState> {
  state: ErrState = { error: null }
  static getDerivedStateFromError(error: Error): ErrState { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('[ErrorBoundary]', error, info) }
  render() {
    if (this.state.error) {
      return (
        <div style={{ color: 'var(--err, #f87171)', padding: 32, fontFamily: 'monospace' }}>
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
  const { setOverlaySystem, setCostsData, addAgentStep, setAnalyticsSummary, setChromaStats } = useStore(
    useShallow((s) => ({
      setOverlaySystem:    s.setOverlaySystem,
      setCostsData:        s.setCostsData,
      addAgentStep:        s.addAgentStep,
      setAnalyticsSummary: s.setAnalyticsSummary,
      setChromaStats:      s.setChromaStats,
    }))
  )
  useWebSocket()

  /* ── Hydrate agent steps on mount ── */
  useEffect(() => {
    const DEV_MOCK_STEPS = import.meta.env.DEV ? [
      { id: 'mock-1', agent: 'watcher',  taskId: 'task-001', stepNumber: 1, stepType: 'llm',   description: 'Analisi trend prezzi concorrenti Etsy',            durationMs: 1240, timestamp: new Date(Date.now() - 180_000).toISOString() },
      { id: 'mock-2', agent: 'recall',   taskId: 'task-002', stepNumber: 1, stepType: 'think', description: 'Sintesi risultati query "branding handmade"',        durationMs:  880, timestamp: new Date(Date.now() -  90_000).toISOString() },
      { id: 'mock-3', agent: 'remind',   taskId: 'task-003', stepNumber: 1, stepType: 'tool',  description: 'Scrittura reminder su database locale',               durationMs:  320, timestamp: new Date(Date.now() -  12_000).toISOString() },
    ] : []

    fetch('/api/agents/steps/recent?limit=50')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!Array.isArray(data?.steps) || data.steps.length === 0) {
          // backend offline → inject dev mock data so reasoning panel looks populated
          DEV_MOCK_STEPS.forEach((s) => addAgentStep(s))
          return
        }
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
      .catch(() => {
        // fetch failed entirely → inject dev mock data
        DEV_MOCK_STEPS.forEach((s) => addAgentStep(s))
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ── Fetch costs / analytics / chroma ogni 30s ── */
  useEffect(() => {
    const fetchCosts = () =>
      fetch('/api/costs?days=30')
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          if (!data?.breakdown) return
          const b = data.breakdown
          const budgetUsd = b.budget_threshold_eur ? b.budget_threshold_eur / 0.92 : undefined
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
        .then((data) => {
          if (data?.summary) { setAnalyticsSummary(data.summary); return }
          // offline dev mock
          if (import.meta.env.DEV) setAnalyticsSummary({ days: 14, total: 263, completed: 263, failed: 0, running: 0, by_status: {}, per_day: {}, per_agent: {}, production_queue: {} })
        })
        .catch(() => {
          if (import.meta.env.DEV) setAnalyticsSummary({ days: 14, total: 263, completed: 263, failed: 0, running: 0, by_status: {}, per_day: {}, per_agent: {}, production_queue: {} })
        })

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
      <div className="shell">
        <Header />

        {/* ── Content: main (1fr) + right col (360px) ── */}
        <div className="content">

          {/* ── Main: orb-zone (1fr) + reasoning (200px) ── */}
          <main className="main">
            <div className="orb-zone">
              <PepeOrb />
              <ToolActivityChip />
              <VoiceNotificationStack />
            </div>
            <div className="reasoning">
              <ReasoningPanel />
            </div>
          </main>

          {/* ── Right col: personal qcard + analytics qcard (50/50) ── */}
          <aside className="right-col">
            <div className="qcard">
              <PersonalQuickCard onOpen={() => setOverlaySystem('personal')} />
            </div>
            <div className="qcard">
              <AnalyticsMiniPanel onOpen={() => setAnalyticsOpen(true)} />
            </div>
          </aside>
        </div>

        {/* ── Domains bar: 180px full-width bottom ── */}
        <div className="domains">
          <DomainCard />
        </div>
      </div>

      {/* ── Overlays ── */}
      <AnalyticsOverlay open={analyticsOpen} onClose={() => setAnalyticsOpen(false)} />
      <SystemOverlay />
    </ErrorBoundary>
  )
}
