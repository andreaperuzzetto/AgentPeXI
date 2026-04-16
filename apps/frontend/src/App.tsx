import { useState, useEffect } from 'react'
import { Header } from './components/Header'
import { ChatPanel } from './components/Chat/ChatPanel'
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
import { useWebSocket } from './hooks/useWebSocket'
import { useStore } from './store'

export default function App() {
  const [chatCollapsed, setChatCollapsed] = useState(false)
  const [analyticsOpen, setAnalyticsOpen] = useState(false)
  const [sistemiTab, setSistemiTab] = useState<'dominio' | 'tool'>('tool')
  const setCostsData = useStore((s) => s.setCostsData)
  const setAnalyticsSummary = useStore((s) => s.setAnalyticsSummary)
  const setChromaStats = useStore((s) => s.setChromaStats)
  useWebSocket()

  /* ── Fetch real cost data from backend on mount ── */
  useEffect(() => {
    fetch('/api/costs?days=30')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!data?.breakdown) return
        const b = data.breakdown
        // budget_threshold_eur → USD approx (÷ 0.92)
        const budgetUsd = b.budget_threshold_eur ? b.budget_threshold_eur / 0.92 : undefined
        setCostsData({
          total:    b.total    ?? 0,
          perAgent: b.per_agent ?? {},
          perDay:   b.per_day   ?? {},
          budgetMonthlyUsd: budgetUsd,
        })
      })
      .catch(() => { /* ignore — fallback to accumulated WS data */ })
  }, [setCostsData])

  /* ── Fetch analytics summary on mount ── */
  useEffect(() => {
    fetch('/api/analytics/summary?days=14')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.summary) setAnalyticsSummary(data.summary)
      })
      .catch(() => {})
  }, [setAnalyticsSummary])

  /* ── Fetch ChromaDB stats on mount ── */
  useEffect(() => {
    fetch('/api/memory/stats')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.chroma) setChromaStats(data.chroma)
      })
      .catch(() => {})
  }, [setChromaStats])

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--base)' }}>
      <Header />

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* ── Sidebar chat ── */}
        <aside
          style={{
            width: chatCollapsed ? 0 : 290,
            minWidth: chatCollapsed ? 0 : 290,
            flexShrink: 0,
            borderRight: '1px solid var(--b0)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            transition: 'width .3s var(--e-out), min-width .3s var(--e-out)',
          }}
        >
          <ChatPanel onCollapse={() => setChatCollapsed(true)} />
        </aside>

        {/* ── Chat FAB (quando collassata) ── */}
        {chatCollapsed && (
          <button
            onClick={() => setChatCollapsed(false)}
            title="Apri chat"
            style={{
              position: 'fixed',
              bottom: 20,
              left: 20,
              zIndex: 40,
              width: 46,
              height: 46,
              borderRadius: '50%',
              background: 'var(--accent)',
              border: 'none',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 4px 16px rgba(0,0,0,.5), 0 0 24px rgba(45,232,106,.18)',
              transition: 'transform .2s var(--e-spring), box-shadow .2s var(--e-io)',
            }}
            onMouseEnter={(e) => {
              const el = e.currentTarget as HTMLElement
              el.style.transform = 'scale(1.1)'
              el.style.boxShadow = '0 6px 20px rgba(0,0,0,.55), 0 0 32px rgba(45,232,106,.28)'
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget as HTMLElement
              el.style.transform = 'scale(1)'
              el.style.boxShadow = '0 4px 16px rgba(0,0,0,.5), 0 0 24px rgba(45,232,106,.18)'
            }}
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M10 2C5.58 2 2 5.36 2 9.5c0 2.08.87 3.96 2.27 5.33L3.5 18l3.35-1.1A8.1 8.1 0 0 0 10 17c4.42 0 8-3.36 8-7.5S14.42 2 10 2z" fill="#0b0c0b"/>
              <circle cx="7" cy="9.5" r="1" fill="#0b0c0b" opacity=".7"/>
              <circle cx="10" cy="9.5" r="1" fill="#0b0c0b"/>
              <circle cx="13" cy="9.5" r="1" fill="#0b0c0b" opacity=".7"/>
            </svg>
          </button>
        )}

        {/* ── Center column ── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>

          {/* Pepe — top 50% */}
          <div style={{
            flex: '0 0 50%',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            borderBottom: '2px solid var(--b1)',
          }}>
            <ReasoningPanel />
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
                    fontFamily: 'var(--fd)', fontSize: 10, letterSpacing: '0.05em',
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

        {/* ── Right column ── */}
        <div
          style={{
            width: 340,
            flexShrink: 0,
            borderLeft: '1px solid var(--b0)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
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
        </div>
      </div>

      {/* System overlay modal */}
      <SystemOverlay />
      {/* Analytics overlay modal */}
      <AnalyticsOverlay open={analyticsOpen} onClose={() => setAnalyticsOpen(false)} />
      {/* Task detail overlay */}
      <TaskDetailOverlay />
    </div>
  )
}
