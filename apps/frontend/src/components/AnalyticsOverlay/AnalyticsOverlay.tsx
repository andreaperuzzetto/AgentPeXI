import { useEffect } from 'react'
import { useStore } from '../../store'
import './AnalyticsOverlay.css'

interface Props {
  open: boolean
  onClose: () => void
}

export function AnalyticsOverlay({ open, onClose }: Props) {
  const llmStats       = useStore((s) => s.llmStats)
  const summary     = useStore((s) => s.analyticsSummary)
  const chromaStats = useStore((s) => s.chromaStats)
  const budgetMonthlyUsd = useStore((s) => s.budgetMonthlyUsd)

  const totalCost    = llmStats.totalCost
  const todayCost    = llmStats.runCost
  const cache        = llmStats.cacheStats
  const tokens       = llmStats.tokenStats
  const tokensPerDay = llmStats.tokensPerDay

  // Helper: formatta numero di token in forma leggibile (es. 1.2M, 450K)
  const fmtTok = (n: number) =>
    n >= 1_000_000 ? `${(n / 1_000_000).toFixed(2)}M`
    : n >= 1_000   ? `${(n / 1_000).toFixed(1)}K`
    : String(n)

  // Helper: day-of-week abbreviation da stringa YYYY-MM-DD
  const DOW = ['D','L','M','M','G','V','S']
  const dayLabel = (dateStr: string) => {
    const d = new Date(dateStr + 'T12:00:00')
    return `${DOW[d.getDay()]} ${d.getDate()}`
  }

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

  // Ultimi 7 giorni con date reali
  const last7 = Object.entries(llmStats.perDay)
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(-7)
  const last7Max = Math.max(...last7.map(([, v]) => v), 0.001)

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onClose])

  const dayEntries = Object.entries(llmStats.perDay).sort(([a], [b]) => a.localeCompare(b))
  const dayValues = dayEntries.map(([, v]) => v)
  const maxDay = Math.max(...dayValues, 0.001)
  const minDay = dayValues.length > 0 ? Math.min(...dayValues) : 0
  const avgDay = dayValues.length > 0 ? dayValues.reduce((s, v) => s + v, 0) / dayValues.length : 0
  const monthlyProjection = avgDay * 30
  const budgetUsd = budgetMonthlyUsd ?? 0
  const budgetPct = budgetUsd > 0 ? (monthlyProjection / budgetUsd) * 100 : 0

  if (!open) return null

  const W = 260, H = 60, PAD = 4
  const sparkPoints = dayValues.length > 1
    ? dayValues.map((v, i) => {
        const x = PAD + (i / (dayValues.length - 1)) * (W - 2 * PAD)
        const y = H - PAD - (v / maxDay) * (H - 2 * PAD)
        return `${x},${y}`
      }).join(' ')
    : ''
  const firstDate = dayEntries[0]?.[0]
  const lastDate  = dayEntries[dayEntries.length - 1]?.[0]
  const fmtDate = (s: string | undefined) => {
    if (!s) return ''
    const d = new Date(s + 'T12:00:00')
    const MONTHS = ['gen','feb','mar','apr','mag','giu','lug','ago','set','ott','nov','dic']
    return `${d.getDate()} ${MONTHS[d.getMonth()]}`
  }

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
                {budgetPct.toFixed(0)}% budget (${budgetUsd.toFixed(0)})
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
            {last7.length === 0
              ? <span className="an-sub">Nessun dato disponibile</span>
              : <>
            <div className="barchart">
              {last7.map(([date, val], i) => {
                const h = Math.round((val / last7Max) * 100)
                const label = `${dayLabel(date)}: $${val.toFixed(4)}`
                return <div key={i} className="bar" style={{ height: `${h}%` }} title={label} />
              })}
            </div>
            <div className="barlabels">
              {last7.map(([date], i) => (
                <span key={i} className="barlabel">{dayLabel(date)}</span>
              ))}
            </div>
            {/* Valore max/min come riferimento */}
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6 }}>
              <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)' }}>
                min ${Math.min(...last7.map(([,v])=>v)).toFixed(4)}
              </span>
              <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)' }}>
                max ${last7Max.toFixed(4)}
              </span>
            </div>
            </>}
          </AnCard>

          <AnCard title="Andamento costi — 30gg">
            {dayValues.length === 0 ? (
              <span className="an-sub">In attesa dati</span>
            ) : (
              <>
                {/* Range date + asse Y */}
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)' }}>{fmtDate(firstDate)}</span>
                  <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)' }}>{fmtDate(lastDate)}</span>
                </div>
                <div style={{ position: 'relative' }}>
                  {/* Label asse Y — max e min */}
                  <div style={{ position: 'absolute', right: 0, top: 0, display: 'flex', flexDirection: 'column', justifyContent: 'space-between', height: H, pointerEvents: 'none' }}>
                    <span style={{ fontFamily: 'var(--fd)', fontSize: 8, color: 'var(--tf)', lineHeight: 1 }}>${maxDay.toFixed(3)}</span>
                    <span style={{ fontFamily: 'var(--fd)', fontSize: 8, color: 'var(--tf)', lineHeight: 1 }}>${minDay.toFixed(3)}</span>
                  </div>
                  <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: 'block' }}>
                    <defs>
                      <linearGradient id="finSparkFill" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="0%" stopColor="var(--accent)" />
                        <stop offset="100%" stopColor="transparent" />
                      </linearGradient>
                    </defs>
                    {/* Linea media come riferimento */}
                    {dayValues.length > 1 && (
                      <line
                        x1={PAD} x2={W - PAD}
                        y1={H - PAD - (avgDay / maxDay) * (H - 2 * PAD)}
                        y2={H - PAD - (avgDay / maxDay) * (H - 2 * PAD)}
                        stroke="var(--tf)" strokeWidth="0.5" strokeDasharray="3 3"
                      />
                    )}
                    {sparkPoints && (
                      <>
                        <polyline points={`${PAD},${H - PAD} ${sparkPoints} ${W - PAD},${H - PAD}`} fill="url(#finSparkFill)" opacity=".15" />
                        <polyline points={sparkPoints} fill="none" stroke="var(--accent)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
                      </>
                    )}
                    {/* Punti sui nodi — tooltip nativo */}
                    {dayEntries.map(([date, val], i) => {
                      const x = PAD + (i / (dayValues.length - 1)) * (W - 2 * PAD)
                      const y = H - PAD - (val / maxDay) * (H - 2 * PAD)
                      return (
                        <circle key={i} cx={x} cy={y} r={2.5} fill="var(--accent)" opacity={0.7}>
                          <title>{fmtDate(date)}: ${val.toFixed(4)}</title>
                        </circle>
                      )
                    })}
                  </svg>
                </div>
                <div style={{ display: 'flex', gap: 12, marginTop: 8 }}>
                  {[
                    { l: `Totale ${dayValues.length}gg`, v: `$${totalCost.toFixed(2)}`, c: 'var(--tp)' },
                    { l: 'Media/giorno',                 v: `$${avgDay.toFixed(4)}`,    c: 'var(--tm)' },
                    { l: 'Max in un giorno',             v: `$${maxDay.toFixed(4)}`,    c: 'var(--warn)' },
                  ].map((item) => (
                    <div key={item.l} style={{ flex: 1 }}>
                      <div style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{item.l}</div>
                      <div style={{ fontFamily: 'var(--fd)', fontSize: 15, color: item.c, fontWeight: 500, marginTop: 2 }}>{item.v}</div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </AnCard>

          <AnCard title="Token — 30gg" wide>
            {tokens.total === 0 ? (
              <span className="an-sub">Nessun dato token disponibile</span>
            ) : (
              <>
                {/* ── Totale + ratio ── */}
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 10 }}>
                  <div className="an-big" style={{ fontSize: 22 }}>{fmtTok(tokens.total)}</div>
                  <span className="an-sub">token totali · ratio I/O {tokens.output > 0 ? `${(tokens.input / tokens.output).toFixed(1)}:1` : '—'}</span>
                </div>

                {/* ── Barra proporzionale breakdown ── */}
                {(() => {
                  const inp  = tokens.input
                  const out  = tokens.output
                  const cr   = cache.readTokens
                  const tot  = inp + out + cr || 1
                  const pInp = (inp / tot) * 100
                  const pOut = (out / tot) * 100
                  const pCr  = (cr  / tot) * 100
                  return (
                    <div style={{ marginBottom: 12 }}>
                      {/* Barra */}
                      <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', gap: 1 }}>
                        <div style={{ width: `${pInp}%`, background: 'rgba(27,255,94,.25)', transition: 'width .6s var(--eo)' }} title={`Input: ${fmtTok(inp)}`} />
                        <div style={{ width: `${pOut}%`, background: 'rgba(27,255,94,.7)',  transition: 'width .6s var(--eo)' }} title={`Output: ${fmtTok(out)}`} />
                        <div style={{ width: `${pCr}%`,  background: 'rgba(27,255,94,.12)', transition: 'width .6s var(--eo)' }} title={`Cache read: ${fmtTok(cr)}`} />
                      </div>
                      {/* Legenda */}
                      <div style={{ display: 'flex', gap: 14, marginTop: 6 }}>
                        {[
                          { l: 'Input',      v: fmtTok(inp), p: pInp, c: 'rgba(27,255,94,.35)' },
                          { l: 'Output',     v: fmtTok(out), p: pOut, c: 'rgba(27,255,94,.8)'  },
                          { l: 'Cache read', v: fmtTok(cr),  p: pCr,  c: 'rgba(27,255,94,.18)' },
                        ].map((item) => (
                          <div key={item.l} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                            <span style={{ width: 8, height: 8, borderRadius: 2, background: item.c, flexShrink: 0 }} />
                            <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)' }}>{item.l}</span>
                            <span style={{ fontFamily: 'var(--fd)', fontSize: 10, color: 'var(--tm)', marginLeft: 2 }}>{item.v}</span>
                            <span style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)' }}>({item.p.toFixed(0)}%)</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                })()}

                {/* ── Grafico stacked per giorno (7gg) ── */}
                {(() => {
                  const entries = Object.entries(tokensPerDay)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .slice(-7)
                  if (entries.length < 2) return null
                  const maxTotal = Math.max(...entries.map(([, v]) => v.input + v.output), 1)
                  const BAR_H = 56
                  return (
                    <div>
                      <div style={{ fontFamily: 'var(--fd)', fontSize: 9, color: 'var(--tf)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: 6 }}>
                        Input / Output per giorno — 7gg
                      </div>
                      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: BAR_H }}>
                        {entries.map(([date, v]) => {
                          const total  = v.input + v.output || 0
                          const hTotal = Math.round((total / maxTotal) * BAR_H)
                          const hOut   = total > 0 ? Math.round((v.output / total) * hTotal) : 0
                          const hInp   = hTotal - hOut
                          const dow    = ['D','L','M','M','G','V','S'][new Date(date + 'T12:00:00').getDay()]
                          const day    = new Date(date + 'T12:00:00').getDate()
                          const tip    = `${dow} ${day}: Input ${fmtTok(v.input)} · Output ${fmtTok(v.output)}`
                          return (
                            <div key={date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0, cursor: 'default' }} title={tip}>
                              <div style={{ width: '100%', display: 'flex', flexDirection: 'column', borderRadius: '2px 2px 0 0', overflow: 'hidden', height: hTotal }}>
                                <div style={{ height: hOut, background: 'rgba(27,255,94,.7)', flexShrink: 0 }} />
                                <div style={{ height: hInp, background: 'rgba(27,255,94,.2)', flexShrink: 0 }} />
                              </div>
                            </div>
                          )
                        })}
                      </div>
                      <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                        {entries.map(([date]) => {
                          const dow = ['D','L','M','M','G','V','S'][new Date(date + 'T12:00:00').getDay()]
                          const day = new Date(date + 'T12:00:00').getDate()
                          return (
                            <span key={date} style={{ flex: 1, textAlign: 'center', fontFamily: 'var(--fd)', fontSize: 8, color: 'var(--tf)' }}>
                              {`${dow}\n${day}`}
                            </span>
                          )
                        })}
                      </div>
                    </div>
                  )
                })()}

                {/* ── Metriche cache ── */}
                <div className="metric-list" style={{ marginTop: 10 }}>
                  {[
                    { l: 'Cache write', v: fmtTok(cache.writeTokens), vc: 'var(--tf)' },
                    { l: 'Cache read',  v: fmtTok(cache.readTokens),  vc: 'var(--ok)' },
                    { l: 'Efficienza',  v: `${cache.efficiencyPct.toFixed(1)}%`, vc: cache.efficiencyPct >= 50 ? 'var(--ok)' : 'var(--warn)' },
                    { l: 'Risparmio',   v: `$${cache.savingsUsd.toFixed(4)}`, vc: 'var(--ok)' },
                  ].map((row) => (
                    <div key={row.l} className="mi">
                      <span className="mi-l">{row.l}</span>
                      <span className="mi-v" style={{ color: row.vc }}>{row.v}</span>
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
