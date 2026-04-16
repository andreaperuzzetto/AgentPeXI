import { useState, useEffect } from 'react'
import { useStore } from '../../store'
import type { CostsBreakdown } from '../../types'
import './AnalyticsOverlay.css'

interface Props {
  open: boolean
  onClose: () => void
}

const FALLBACK_SPARKLINE = [30, 55, 42, 70, 35, 20, 60]
const DAYS_LABELS = ['L', 'M', 'M', 'G', 'V', 'S', 'D']

export function AnalyticsOverlay({ open, onClose }: Props) {
  const dailyCost = useStore((s) => s.systemStatus.dailyCost)
  const llmStats  = useStore((s) => s.llmStats)
  const summary   = useStore((s) => s.analyticsSummary)
  const chromaStats = useStore((s) => s.chromaStats)

  const [activeTab, setActiveTab] = useState<'analytics' | 'finance'>('analytics')
  const [costsData, setCostsLocal] = useState<CostsBreakdown | null>(null)
  const [costsLoading, setCostsLoading] = useState(false)

  /* Real data derived from store */
  const totalCost = llmStats.totalCost > 0 ? llmStats.totalCost : 1.84

  /* Per-agent bars from REST /api/costs or fallback */
  const agentBars = (() => {
    const entries = Object.entries(llmStats.perAgent)
    if (entries.length === 0) {
      return [
        { name: 'Research',  pct: 72, val: '$—' },
        { name: 'Analytics', pct: 18, val: '$—' },
        { name: 'Design',    pct: 9,  val: '$—' },
        { name: 'Publisher', pct: 3,  val: '$—' },
      ]
    }
    const maxCost = Math.max(...entries.map(([, v]) => v), 0.001)
    return entries
      .sort(([, a], [, b]) => b - a)
      .slice(0, 4)
      .map(([name, cost]) => ({
        name: name.charAt(0).toUpperCase() + name.slice(1),
        pct:  Math.round((cost / maxCost) * 100),
        val:  `$${cost.toFixed(2)}`,
      }))
  })()

  /* Sparkline from per_day or fallback */
  const sparkline = (() => {
    const days = Object.entries(llmStats.perDay)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-7)
      .map(([, v]) => v)
    if (days.length === 0) return FALLBACK_SPARKLINE
    const max = Math.max(...days, 0.001)
    return days.map((v) => Math.round((v / max) * 100))
  })()

  const todayCost    = llmStats.runCost > 0 ? llmStats.runCost : (dailyCost ?? 0.042)

  // ESC close
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  /* Lazy fetch costs when Finance tab opens */
  useEffect(() => {
    if (!open || activeTab !== 'finance' || costsData) return
    setCostsLoading(true)
    fetch('/api/costs?days=30')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.breakdown) setCostsLocal(data.breakdown)
      })
      .catch(() => {})
      .finally(() => setCostsLoading(false))
  }, [open, activeTab, costsData])

  if (!open) return null

  return (
    <div
      className="an-backdrop"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="an-modal">

        {/* Header */}
        <div className="an-modal-head">
          <span style={{ fontFamily: 'var(--fh)', fontSize: 15, fontWeight: 700, letterSpacing: '0.04em', color: 'var(--tp)' }}>
            Analytics
          </span>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tf)', marginLeft: 10 }}>
            Aggiornato {new Date().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}
          </span>
          <div style={{ display: 'flex', gap: 4, marginLeft: 16 }}>
            {(['analytics', 'finance'] as const).map(tab => (
              <button key={tab} onClick={() => setActiveTab(tab)} style={{
                background: activeTab === tab ? 'var(--adim)' : 'none',
                border: `1px solid ${activeTab === tab ? 'rgba(45,232,106,.28)' : 'var(--b0)'}`,
                borderRadius: 6, padding: '3px 12px', cursor: 'pointer',
                fontFamily: 'var(--fd)', fontSize: 10,
                color: activeTab === tab ? 'var(--accent)' : 'var(--tf)',
                transition: 'all .2s var(--e-io)',
              }}>
                {tab === 'analytics' ? 'Analytics' : 'Finance'}
              </button>
            ))}
          </div>
          <div style={{ flex: 1 }} />
          <button
            onClick={onClose}
            className="an-close-btn"
          >
            ✕ Chiudi
          </button>
        </div>

        {/* Body */}
        {activeTab === 'analytics' ? (
        <div className="an-modal-body">

          {/* Costo Totale */}
          <AnCard title="Costo Totale">
            <div className="an-big">${totalCost.toFixed(2)}</div>
            <div className="an-sub">Da inizio progetto</div>
            <div className="an-delta">↑ ${todayCost.toFixed(3)} oggi</div>
          </AnCard>

          {/* Proiezione Mensile */}
          <AnCard title="Proiezione Mensile">
            <div className="an-big">${(todayCost * 30).toFixed(2)}</div>
            <div className="an-sub">Da ultimi 7 giorni</div>
            <div className="an-delta">Budget residuo: ${Math.max(0, 5 - totalCost).toFixed(2)}</div>
          </AnCard>

          {/* Task Completati */}
          <AnCard title="Task Completati">
            <div className="an-big">{summary?.total ?? '—'}</div>
            <div className="an-sub">{summary ? `${summary.completed} ok · ${summary.failed} errori` : '—'}</div>
            <div className="an-delta">{summary?.running ? `↑ ${summary.running} in corso` : '—'}</div>
          </AnCard>

          {/* Costo per Agente */}
          <AnCard title="Costo per Agente">
            <div style={{ marginTop: 4 }}>
              {agentBars.map((ab) => (
                <div key={ab.name} className="abr-row">
                  <span className="abr-name">{ab.name}</span>
                  <div className="abr-wrap">
                    <div className="abr-fill" style={{ width: `${ab.pct}%` }} />
                  </div>
                  <span className="abr-val">{ab.val}</span>
                </div>
              ))}
            </div>
          </AnCard>

          {/* Costo Giornaliero — barchart */}
          <AnCard title="Costo Giornaliero — 7gg">
            <div className="barchart">
              {sparkline.map((h, i) => (
                <div key={i} className="bar" style={{ height: `${h}%` }} />
              ))}
            </div>
            <div className="barlabels">
              {DAYS_LABELS.slice(0, sparkline.length).map((d, i) => <span key={i} className="barlabel">{d}</span>)}
            </div>
          </AnCard>

          {/* Learning Loop */}
          <AnCard title="Learning Loop">
            <div className="metric-list">
              {[
                { l: 'failure_analysis', v: String(summary?.by_status?.failed ?? 0),   vc: 'var(--accent)' },
                { l: 'success_pattern',  v: String(summary?.by_status?.completed ?? 0), vc: 'var(--accent)' },
                { l: 'design_outcome',   v: String(summary?.per_agent?.design?.completed ?? 0), vc: 'var(--accent)' },
                { l: 'chroma_entries',   v: String(chromaStats?.count ?? 0),            vc: 'var(--tm)' },
                { l: 'Ultimo update',    v: new Date().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' }), vc: 'var(--tm)' },
              ].map((row) => (
                <div key={row.l} className="mi">
                  <span className="mi-l">{row.l}</span>
                  <span className="mi-v" style={{ color: row.vc }}>{row.v}</span>
                </div>
              ))}
            </div>
          </AnCard>

          {/* Pipeline — ultimi 14 giorni (wide) */}
          <AnCard title="Pipeline — Ultimi 14 giorni" wide>
            <div className="pipe-grid">
              {[
                { l: 'Run totali',    v: String(summary?.total ?? 0),                              vc: 'var(--tp)' },
                { l: 'Completate',   v: String(summary?.completed ?? 0),                           vc: 'var(--ok)' },
                { l: 'Parziali',     v: String(summary?.by_status?.partial ?? 0),                   vc: 'var(--warn)' },
                { l: 'Fallite',      v: String(summary?.failed ?? 0),                               vc: 'var(--err)' },
                { l: 'Listing creati', v: String(summary?.production_queue?.completed ?? 0),         vc: 'var(--accent)' },
              ].map((row) => (
                <div key={row.l}>
                  <div className="pi-l">{row.l}</div>
                  <div className="pi-v" style={{ color: row.vc }}>{row.v}</div>
                </div>
              ))}
            </div>
          </AnCard>

        </div>
        ) : (
          <FinanceTab costsData={costsData} loading={costsLoading} />
        )}
      </div>
    </div>
  )
}

function FinanceTab({ costsData, loading }: { costsData: CostsBreakdown | null; loading: boolean }) {
  if (loading) {
    return (
      <div className="an-modal-body">
        {[0, 1, 2].map((i) => (
          <div key={i} className="card an-card" style={{ padding: '16px 18px', animation: `card-up .3s var(--e-out) ${i * 0.05 + 0.05}s both` }}>
            <div style={{ height: 14, width: '40%', background: 'var(--s3)', borderRadius: 4, marginBottom: 12 }} />
            <div style={{ height: 10, width: '70%', background: 'var(--s2)', borderRadius: 3, marginBottom: 8 }} />
            <div style={{ height: 10, width: '55%', background: 'var(--s2)', borderRadius: 3 }} />
          </div>
        ))}
      </div>
    )
  }

  if (!costsData) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)' }}>
          In attesa dei primi run agenti
        </span>
      </div>
    )
  }

  const { per_agent, per_day, total, budget_threshold_eur } = costsData
  const perTool = costsData.per_tool ?? {}
  const budgetUsd = budget_threshold_eur > 0 ? budget_threshold_eur / 0.92 : 0

  const agentEntries = Object.entries(per_agent).sort(([, a], [, b]) => b - a)
  const maxAgentCost = Math.max(...agentEntries.map(([, v]) => v), 0.001)

  const toolEntries = Object.entries(perTool).sort(([, a], [, b]) => b - a)
  const toolTotal = toolEntries.reduce((s, [, v]) => s + v, 0) || 1

  const dayEntries = Object.entries(per_day).sort(([a], [b]) => a.localeCompare(b))
  const dayValues = dayEntries.map(([, v]) => v)
  const maxDay = Math.max(...dayValues, 0.001)
  const avgDay = dayValues.length > 0 ? dayValues.reduce((s, v) => s + v, 0) / dayValues.length : 0
  const monthlyProjection = avgDay * 30
  const budgetPct = budgetUsd > 0 ? (monthlyProjection / budgetUsd) * 100 : 0

  const W = 260, H = 60, PAD = 4
  const sparkPoints = dayValues.length > 1
    ? dayValues.map((v, i) => {
        const x = PAD + (i / (dayValues.length - 1)) * (W - 2 * PAD)
        const y = H - PAD - (v / maxDay) * (H - 2 * PAD)
        return `${x},${y}`
      }).join(' ')
    : ''

  return (
    <div className="an-modal-body">

      {/* Card 1 — Costo per agente */}
      <AnCard title="Costo per agente">
        <div style={{ marginTop: 4 }}>
          {agentEntries.length === 0 ? (
            <div style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tf)' }}>Nessun dato</div>
          ) : agentEntries.map(([name, cost]) => {
            const pct = Math.round((cost / maxAgentCost) * 100)
            const eur = cost * 0.92
            return (
              <div key={name} className="abr-row">
                <span className="abr-name">{name.charAt(0).toUpperCase() + name.slice(1)}</span>
                <div className="abr-wrap">
                  <div className="abr-fill" style={{ width: `${pct}%` }} />
                </div>
                <span style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tp)', flexShrink: 0, whiteSpace: 'nowrap' }}>
                  ${cost.toFixed(4)}
                  <span style={{ color: 'var(--tf)', marginLeft: 4 }}>€{eur.toFixed(4)}</span>
                </span>
              </div>
            )
          })}
        </div>
      </AnCard>

      {/* Card 2 — Breakdown strumenti */}
      <AnCard title="Breakdown strumenti">
        <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 0 }}>
          {toolEntries.length === 0 ? (
            <div style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tf)' }}>Nessun dato</div>
          ) : toolEntries.slice(0, 8).map(([name, cost]) => {
            const pct = Math.round((cost / toolTotal) * 100)
            return (
              <div key={name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '5px 0', borderBottom: '1px solid var(--b0)' }}>
                <span style={{
                  fontFamily: 'var(--fd)', fontSize: 10,
                  padding: '1px 7px', borderRadius: 4,
                  background: 'var(--s3)', color: 'var(--tm)',
                }}>
                  {name}
                </span>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tf)' }}>{pct}%</span>
                  <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--warn)' }}>${cost.toFixed(4)}</span>
                </div>
              </div>
            )
          })}
        </div>
      </AnCard>

      {/* Card 3 — Trend giornaliero */}
      <AnCard title="Andamento costi 30gg">
        {dayValues.length === 0 ? (
          <div style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tf)', padding: '12px 0' }}>Nessun dato</div>
        ) : (
          <>
            <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ marginTop: 6 }}>
              <defs>
                <linearGradient id="finSparkFill" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="var(--accent)" />
                  <stop offset="100%" stopColor="transparent" />
                </linearGradient>
              </defs>
              {sparkPoints && (
                <>
                  <polyline
                    points={`${PAD},${H - PAD} ${sparkPoints} ${W - PAD},${H - PAD}`}
                    fill="url(#finSparkFill)" opacity=".15"
                  />
                  <polyline
                    points={sparkPoints}
                    fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round"
                  />
                </>
              )}
            </svg>
            <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
              {[
                { l: 'Totale 30gg', v: `$${total.toFixed(2)}`, c: 'var(--tp)' },
                { l: 'Media/giorno', v: `$${avgDay.toFixed(4)}`, c: 'var(--tm)' },
                { l: 'Proiezione mensile', v: `$${monthlyProjection.toFixed(2)}`, c: budgetPct > 80 ? 'var(--warn)' : 'var(--ok)' },
              ].map((item) => (
                <div key={item.l} style={{ flex: 1 }}>
                  <div style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{item.l}</div>
                  <div style={{ fontFamily: 'var(--fd)', fontSize: 13, color: item.c, fontWeight: 500, marginTop: 2 }}>{item.v}</div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 6, fontFamily: 'var(--fd)', fontSize: 10, color: budgetPct > 80 ? 'var(--warn)' : 'var(--ok)' }}>
              {budgetPct.toFixed(0)}% del budget (€{budget_threshold_eur.toFixed(0)} / ${budgetUsd.toFixed(0)})
            </div>
          </>
        )}
      </AnCard>

    </div>
  )
}

function AnCard({ title, children, wide }: { title: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={`card an-card${wide ? ' wide' : ''}`}>
      <div className="an-t">{title}</div>
      {children}
    </div>
  )
}
