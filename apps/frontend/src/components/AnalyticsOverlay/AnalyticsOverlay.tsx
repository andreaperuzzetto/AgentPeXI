import { useEffect } from 'react'
import { useStore } from '../../store'
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
          <div style={{ flex: 1 }} />
          <button
            onClick={onClose}
            className="an-close-btn"
          >
            ✕ Chiudi
          </button>
        </div>

        {/* Body — 3-col grid */}
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
