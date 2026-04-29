/**
 * Shell — root layout component (sostituisce App.tsx)
 *
 * Struttura:
 *   Sidebar  (fixed left 64px)
 *   Header   (fixed top 56px, left 64px)
 *   <main>   (margin-left 64, margin-top 56)
 *     AnimatePresence → view attiva (neural | etsy | personal | system | analytics)
 *   ContextBrief  (overlay top-level)
 *   VoiceNotifications (overlay top-level)
 *
 * Tutti i fetch periodici (costs, analytics, chroma, domain config, agent steps)
 * sono stati spostati qui da App.tsx.
 */
import { useState, useEffect, Component, type ReactNode, type ErrorInfo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useShallow } from 'zustand/react/shallow'

import { Sidebar } from './Sidebar'
import { Header } from './Header'
import { NeuralView } from '../views/NeuralView'
import { EtsyView } from '../views/EtsyView'
import { PersonalView } from '../views/PersonalView'
import { SystemView } from '../views/SystemView'
import { AnalyticsView } from '../views/AnalyticsView'
import { ContextOverlay } from '../components/ContextOverlay/ContextOverlay'
import { VoiceNotificationStack } from '../components/VoiceNotification/VoiceNotificationStack'
import { AnalyticsOverlay } from '../components/AnalyticsOverlay/AnalyticsOverlay'
import { SystemOverlay } from '../components/SystemOverlay/SystemOverlay'
import { useWebSocket } from '../hooks/useWebSocket'
import { useStore } from '../store'

// ─── Transition spring ────────────────────────────────────────────────────────
const VIEW_VARIANTS = {
  initial:  { opacity: 0, y: 8 },
  animate:  { opacity: 1, y: 0 },
  exit:     { opacity: 0, y: -8 },
}
const VIEW_TRANSITION = {
  type: 'spring' as const,
  stiffness: 100,
  damping: 20,
  duration: 0.25,
}

// ─── View map ─────────────────────────────────────────────────────────────────
const VIEWS: Record<string, ReactNode> = {
  neural:    <NeuralView />,
  etsy:      <EtsyView />,
  personal:  <PersonalView />,
  system:    <SystemView />,
  analytics: <AnalyticsView />,
}

// ─── Error boundary ───────────────────────────────────────────────────────────
interface ErrState { error: Error | null }
class ErrorBoundary extends Component<{ children: ReactNode }, ErrState> {
  state: ErrState = { error: null }
  static getDerivedStateFromError(error: Error): ErrState { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[Shell ErrorBoundary]', error, info)
  }
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

// ─── Shell ────────────────────────────────────────────────────────────────────
export function Shell() {
  const activeZone   = useStore((s) => s.activeZone)
  const briefOpen    = useStore((s) => s.briefOpen)
  const setBriefOpen = useStore((s) => s.setBriefOpen)
  const [analyticsOpen, setAnalyticsOpen] = useState(false)

  const {
    setCostsData,
    addAgentStep,
    setAnalyticsSummary,
    setChromaStats,
    setDomainConfig,
  } = useStore(
    useShallow((s) => ({
      setCostsData:        s.setCostsData,
      addAgentStep:        s.addAgentStep,
      setAnalyticsSummary: s.setAnalyticsSummary,
      setChromaStats:      s.setChromaStats,
      setDomainConfig:     s.setDomainConfig,
    }))
  )

  useWebSocket()

  // ── Fetch domain config on mount ───────────────────────────────────────────
  useEffect(() => {
    fetch('/api/domains/config')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.etsy && data?.personal) setDomainConfig(data)
      })
      .catch(() => {})
  }, [setDomainConfig])

  // ── Hydrate agent steps on mount ───────────────────────────────────────────
  useEffect(() => {
    const DEV_MOCK_STEPS = import.meta.env.DEV ? [
      { id: 'mock-1', agent: 'watcher',  taskId: 'task-001', stepNumber: 1, stepType: 'llm',   description: 'Analisi trend prezzi concorrenti Etsy',       durationMs: 1240, timestamp: new Date(Date.now() - 180_000).toISOString() },
      { id: 'mock-2', agent: 'recall',   taskId: 'task-002', stepNumber: 1, stepType: 'think', description: 'Sintesi risultati query "branding handmade"',  durationMs:  880, timestamp: new Date(Date.now() -  90_000).toISOString() },
      { id: 'mock-3', agent: 'remind',   taskId: 'task-003', stepNumber: 1, stepType: 'tool',  description: 'Scrittura reminder su database locale',        durationMs:  320, timestamp: new Date(Date.now() -  12_000).toISOString() },
    ] : []

    fetch('/api/agents/steps/recent?limit=50')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!Array.isArray(data?.steps) || data.steps.length === 0) {
          DEV_MOCK_STEPS.forEach((s) => addAgentStep(s))
          return
        }
        data.steps.forEach((s: {
          id: number; task_id: string; agent_name: string;
          step_number: number; step_type: string; description: string;
          duration_ms: number; timestamp: string
        }) => {
          addAgentStep({
            id:          String(s.id),
            agent:       s.agent_name,
            taskId:      s.task_id,
            stepNumber:  s.step_number,
            stepType:    s.step_type,
            description: s.description ?? '',
            durationMs:  s.duration_ms ?? 0,
            timestamp:   s.timestamp,
          })
        })
      })
      .catch(() => {
        DEV_MOCK_STEPS.forEach((s) => addAgentStep(s))
      })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Periodic fetches: costs / analytics / chroma ogni 30s ─────────────────
  useEffect(() => {
    const fetchCosts = () =>
      fetch('/api/costs?days=30')
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          if (!data?.breakdown) return
          const b = data.breakdown
          const usdEurRate   = b.usd_eur_rate ?? 0.92
          const budgetUsd    = b.budget_threshold_eur ? b.budget_threshold_eur / usdEurRate : undefined
          const today        = new Date().toISOString().split('T')[0]
          const todayFromDb  = (b.per_day as Record<string, number>)?.[today] ?? 0
          const c = b.cache as { read_tokens: number; write_tokens: number; savings_usd: number; efficiency_pct: number } | undefined
          const t  = b.tokens as { input: number; output: number; total: number } | undefined
          const td = b.tokens_per_day as Record<string, { input: number; output: number; cache_read: number }> | undefined
          setCostsData({
            total:            b.total     ?? 0,
            perAgent:         b.per_agent ?? {},
            perDay:           b.per_day   ?? {},
            budgetMonthlyUsd: budgetUsd,
            runCost:          todayFromDb,
            cacheStats: c ? {
              readTokens:    c.read_tokens,
              writeTokens:   c.write_tokens,
              savingsUsd:    c.savings_usd,
              efficiencyPct: c.efficiency_pct,
            } : undefined,
            tokenStats:   t  ?? undefined,
            tokensPerDay: td ?? undefined,
          })
        })
        .catch(() => {})

    const fetchAnalytics = () =>
      fetch('/api/analytics/summary?days=14')
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          if (data?.summary) { setAnalyticsSummary(data.summary); return }
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
      <div style={{ background: 'var(--bg-base)', minHeight: '100vh', overflow: 'hidden' }}>

        {/* ── Left sidebar — fixed 64px ── */}
        <Sidebar />

        {/* ── Top header — fixed 56px, left 64 ── */}
        <Header />

        {/* ── Main content area ── */}
        <main
          style={{
            marginLeft: 64,
            marginTop: 56,
            height: 'calc(100vh - 56px)',
            overflow: 'hidden',
            position: 'relative',
          }}
        >
          <AnimatePresence mode="wait">
            <motion.div
              key={activeZone}
              variants={VIEW_VARIANTS}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={VIEW_TRANSITION}
              style={{ width: '100%', height: '100%' }}
            >
              {VIEWS[activeZone]}
            </motion.div>
          </AnimatePresence>
        </main>

        {/* ── Top-level overlays ── */}
        <ContextOverlay
          open={briefOpen}
          onClose={() => setBriefOpen(false)}
        />
        <VoiceNotificationStack />

        {/* Legacy overlays — rimangono fino a FE-4/FE-5 */}
        <AnalyticsOverlay
          open={analyticsOpen}
          onClose={() => setAnalyticsOpen(false)}
        />
        <SystemOverlay />

      </div>
    </ErrorBoundary>
  )
}
