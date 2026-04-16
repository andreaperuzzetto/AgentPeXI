import { useState, useEffect } from 'react'
import { useStore } from '../../store'
import type { CostsBreakdown } from '../../types'
import './AnalyticsOverlay.css'

interface Props {
  open: boolean
  onClose: () => void
}

const DAYS_LABELS = ['L', 'M', 'M', 'G', 'V', 'S', 'D']

export function AnalyticsOverlay({ open, onClose }: Props) {
  const llmStats    = useStore((s) => s.llmStats)
  const summary     = useStore((s) => s.analyticsSummary)
  const chromaStats = useStore((s) => s.chromaStats)

  const [costsData, setCostsLocal] = useState<CostsBreakdown | null>(null)

  const totalCost = llmStats.totalCost
  const todayCost = llmStats.runCost

  const agentBars = (() => {
    const entries = Object.entries(llmStats.perAgent)
    if (entries.length === 0) return []
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

  const sparkline = (() => {
    const days = Object.entries(llmStats.perDay)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-7)
      .map(([, v]) => v)
    if (days.length === 0) return []
    const max = Math.max(...days, 0.001)
    return days.map((v) => Math.round((v / max) * 100))
  })()

  useEffect(() => {
    if (!open || costsData) return
    fetch('/api/costs?days=30')
      .then((r) => r.ok ? r.json() : null)
      .then((data) => { if (data?.breakdown) setCostsLocal(data.breakdown) })
      .catch(() => {})
  }, [open, costsData])

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  if (!open) return null

  const dayEntries = costsData ? Object.entries(costsData.per_day).sort(([a], [b]) => a.localeCompare(b)) : []
  const dayValues = dayEntries.map(([, v]) => v)
  const maxDay = Math.max(...dayValues, 0.001)
  const avgDay = dayValues.length > 0 ? dayValues.reduce((s, v) => s + v, 0) / dayValues.length : 0
  const monthlyProjection = avgDay * 30
  const budgetEur = costsData?.budget_threshold_eur ?? 0
  const budgetUsd = budgetEur > 0 ? budgetEur / 0.92 : 0
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
    <div className="an-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="an-modal">
        <div className="an-modal-head">
          <span style={{ fontFamily: 'var(--fh)', fontSize: 19, fontWeight: 700, letterSpacing: '0.04em', color: 'var(--tp)' }}>
            Analytics
          </span>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 14, color: 'var(--tf)', marginLeft: 10 }}>
            Aggiornato {new Date().toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}
          </span>
          <div style={{ flex: 1 }} />
          <button onClick={onClose} className="an-close-btn">✕ Chiudi</button>
        </div>

        <div className="an-modal-body">
          <AnCard title="Costo Totale">
            <div className="an-big">${totalCost.toFixed(2)}</div>
            <div className="an-sub">Da inizio progetto</div>
            <div className="an-delta">↑ ${todayCost.toFixed(3)} oggi</div>
          </AnCard>

          <AnCard title="Proiezione Mensile">
            <div className="an-big">${monthlyProjection.toFixed(2)}</div>
            <div className="an-sub">Media ${avgDay.toFixed(4)}/giorno</div>
            {budgetUsd > 0 && (
              <div className="an-delta" style={{ color: budgetPct > 80 ? 'var(--warn)' : 'var(--ok)' }}>
                {budgetPct.toFixed(0)}% budget (€{budgetEur.toFixed(0)})
              </div>
            )}
          </AnCard>

          <AnCard title="Task Completati">
            <div className="an-big">{summary?.total ?? '—'}</div>
            <div className="an-sub">{summary ? `${summary.completed} ok · ${summary.failed} errori` : '—'}</div>
            <div className="an-delta">{summary?.running ? `↑ ${summary.running} in corso` : '—'}</div>
          </AnCard>

          <AnCard title="Pipeline — Ultimi 14 giorni" wide>
            <div className="pipe-grid">
              {[
                { l: 'Run totali',        v: String(summary?.total ?? 0),                         vc: 'var(--tp)' },
                { l: 'Completate',        v: String(summary?.completed ?? 0),                     vc: 'var(--ok)' },
                { l: 'Parziali',          v: String(summary?.by_status?.partial ?? 0),             vc: 'var(--warn)' },
                { l: 'Fallite',           v: String(summary?.failed ?? 0),                         vc: 'var(--err)' },
                { l: 'Design completati', v: String(summary?.production_queue?.completed ?? 0),    vc: 'var(--accent)' },
              ].map((row) => (
                <div key={row.l}>
                  <div className="pi-l">{row.l}</div>
                  <div className="pi-v" style={{ color: row.vc }}>{row.v}</div>
                </div>
              ))}
            </div>
          </AnCard>

          <AnCard title="Costo per Agente">
            <div style={{ marginTop: 4 }}>
              {agentBars.length === 0
                ? <span className="an-sub">In attesa dei primi run</span>
                : agentBars.map((ab) => (
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

          <AnCard title="Costo Giornaliero — 7gg">
            {sparkline.length === 0
              ? <span className="an-sub">Nessun dato disponibile</span>
              : <>
            <div className="barchart">
              {sparkline.map((h, i) => (
                <div key={i} className="bar" style={{ height: `${h}%` }} />
              ))}
            </div>
            <div className="barlabels">
              {DAYS_LABELS.slice(0, sparkline.length).map((d, i) => <span key={i} className="barlabel">{d}</span>)}
            </div>
            </>}
          </AnCard>

          <AnCard title="Andamento costi — 30gg">
            {dayValues.length === 0 ? (
              <span className="an-sub">In attesa dati</span>
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
                      <polyline points={`${PAD},${H - PAD} ${sparkPoints} ${W - PAD},${H - PAD}`} fill="url(#finSparkFill)" opacity=".15" />
                      <polyline points={sparkPoints} fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
                    </>
                  )}
                </svg>
                <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
                  {[
                    { l: 'Totale 30gg', v: `$${costsData!.total.toFixed(2)}`, c: 'var(--tp)' },
                    { l: 'Media/giorno', v: `$${avgDay.toFixed(4)}`, c: 'var(--tm)' },
                  ].map((item) => (
                    <div key={item.l} style={{ flex: 1 }}>
                      <div style={{ fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--tf)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{item.l}</div>
                      <div style={{ fontFamily: 'var(--fd)', fontSize: 17, color: item.c, fontWeight: 500, marginTop: 2 }}>{item.v}</div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </AnCard>

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
        </div>
      </div>
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
