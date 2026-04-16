import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../../store'
import type { TimelineEntry } from '../../types'
import './TaskDetailOverlay.css'

function fmtTok(n: number): string {
  if (n === 0) return '0'
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

export function TaskDetailOverlay() {
  const selectedTaskId = useStore((s) => s.selectedTaskId)
  const setSelectedTaskId = useStore((s) => s.setSelectedTaskId)
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const [loading, setLoading] = useState(false)

  // ESC close
  useEffect(() => {
    if (!selectedTaskId) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') setSelectedTaskId(null) }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [selectedTaskId, setSelectedTaskId])

  // Fetch timeline
  useEffect(() => {
    if (!selectedTaskId) return
    setLoading(true)
    setTimeline([])
    fetch(`/api/tasks/${encodeURIComponent(selectedTaskId)}/timeline`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.timeline) setTimeline(data.timeline)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [selectedTaskId])

  if (!selectedTaskId) return null

  const llmCalls = timeline.filter((e) => e.type === 'llm_call')
  const totalInputTokens = llmCalls.reduce((s, e) => s + (e.input_tokens ?? 0), 0)
  const totalOutputTokens = llmCalls.reduce((s, e) => s + (e.output_tokens ?? 0), 0)
  const totalCost = llmCalls.reduce((s, e) => s + (e.cost_usd ?? 0), 0)
  const maxTokens = Math.max(...llmCalls.map((c) => (c.input_tokens ?? 0) + (c.output_tokens ?? 0)), 1)

  return createPortal(
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 50,
        background: 'rgba(8,14,10,.82)',
        backdropFilter: 'blur(6px)',
        WebkitBackdropFilter: 'blur(6px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        animation: 'backdrop-in .28s var(--e-io) both',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) setSelectedTaskId(null) }}
    >
      <div style={{
        position: 'relative',
        width: 'min(980px, 90vw)',
        height: 'min(740px, 88vh)',
        background: 'var(--s1)',
        border: '1px solid var(--b1)',
        borderRadius: 18,
        boxShadow: '0 0 0 1px rgba(45,232,106,.06), 0 32px 100px rgba(0,0,0,.8), 0 8px 32px rgba(0,0,0,.5), inset 0 1px 0 rgba(255,255,255,.045)',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
        animation: 'modal-in .3s var(--e-out) both',
        willChange: 'transform, opacity',
      }}>

        {/* Header */}
        <div style={{
          height: 56, flexShrink: 0,
          background: 'var(--s1)',
          borderBottom: '1px solid var(--b0)',
          display: 'flex', alignItems: 'center',
          padding: '0 22px', gap: 12,
        }}>
          <span style={{ fontFamily: 'var(--fh)', fontSize: 17, fontWeight: 700, letterSpacing: '0.04em', color: 'var(--tp)' }}>
            Task Detail
          </span>
          <span style={{
            fontFamily: 'var(--fd)', fontSize: 12,
            padding: '2px 9px', borderRadius: 99,
            border: '1px solid var(--b0)', color: 'var(--tf)',
          }}>
            {selectedTaskId.length > 12 ? selectedTaskId.slice(0, 12) + '…' : selectedTaskId}
          </span>
          <div style={{ flex: 1 }} />
          <button
            onClick={() => setSelectedTaskId(null)}
            style={{
              background: 'none', border: '1px solid var(--b0)',
              borderRadius: 7, padding: '6px 14px',
              color: 'var(--tm)', cursor: 'pointer',
              fontFamily: 'var(--fb)', fontSize: 15,
              transition: 'border-color .25s var(--e-io), color .25s var(--e-io), transform .2s var(--e-spring)',
            }}
            onMouseEnter={(e) => {
              const el = e.currentTarget as HTMLElement
              el.style.borderColor = 'var(--b1)'
              el.style.color = 'var(--tp)'
              el.style.transform = 'scale(1.03)'
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget as HTMLElement
              el.style.borderColor = 'var(--b0)'
              el.style.color = 'var(--tm)'
              el.style.transform = 'scale(1)'
            }}
          >
            ✕ Chiudi
          </button>
        </div>

        {/* Body — 2 columns */}
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

          {/* Left — Timeline (40%) */}
          <div style={{
            width: '40%', borderRight: '1px solid var(--b0)',
            display: 'flex', flexDirection: 'column', overflow: 'hidden',
          }}>
            <div style={{
              padding: '10px 16px', borderBottom: '1px solid var(--b0)', flexShrink: 0,
            }}>
              <span style={{ fontFamily: 'var(--fh)', fontSize: 11, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--tm)' }}>
                Timeline
              </span>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
              {loading ? (
                [0, 1, 2].map((i) => (
                  <div key={i} className="td-skeleton" style={{ animationDelay: `${i * 0.1}s` }} />
                ))
              ) : timeline.length === 0 ? (
                <div style={{ padding: '24px 16px', textAlign: 'center', fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--tf)' }}>
                  Nessun dato per questo task
                </div>
              ) : (
                timeline.map((evt, i) => <TimelineRow key={i} evt={evt} />)
              )}
            </div>
          </div>

          {/* Right — LLM Inspector (60%) */}
          <div style={{
            flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
          }}>
            <div style={{
              padding: '10px 16px', borderBottom: '1px solid var(--b0)', flexShrink: 0,
            }}>
              <span style={{ fontFamily: 'var(--fh)', fontSize: 11, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--tm)' }}>
                LLM Inspector
              </span>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '10px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {loading ? (
                [0, 1, 2].map((i) => (
                  <div key={i} className="td-skeleton" style={{ height: 80, animationDelay: `${i * 0.1}s` }} />
                ))
              ) : llmCalls.length === 0 ? (
                <div style={{ padding: '24px 0', textAlign: 'center', fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--tf)' }}>
                  Nessuna chiamata LLM registrata
                </div>
              ) : (
                <>
                  {llmCalls.map((call, i) => (
                    <LlmCard key={i} call={call} maxTokens={maxTokens} />
                  ))}

                  {/* Totals */}
                  <div style={{
                    marginTop: 'auto', padding: '12px 0', borderTop: '1px solid var(--b0)',
                    display: 'flex', gap: 16,
                  }}>
                    {[
                      { l: 'Input totali', v: fmtTok(totalInputTokens), accent: false },
                      { l: 'Output totali', v: fmtTok(totalOutputTokens), accent: false },
                      { l: 'Costo sessione', v: `$${totalCost.toFixed(4)}`, accent: true },
                    ].map((item) => (
                      <div key={item.l} style={{ flex: 1 }}>
                        <div style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)', textTransform: 'uppercase', letterSpacing: '0.03em' }}>{item.l}</div>
                        <div style={{ fontFamily: 'var(--fd)', fontSize: 16, color: item.accent ? 'var(--accent)' : 'var(--tp)', fontWeight: 500, marginTop: 2 }}>{item.v}</div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}

function TimelineRow({ evt }: { evt: TimelineEntry }) {
  const isStep = evt.type === 'agent_step'
  const isLlm = evt.type === 'llm_call'

  const dotColor = isStep ? 'rgba(96,165,250,.9)' : isLlm ? 'var(--warn)' : 'var(--accent)'

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 10,
      padding: '6px 16px',
      borderBottom: '1px solid var(--b0)',
      transition: 'background .15s',
    }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(45,232,106,.03)' }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
    >
      <span style={{
        width: 7, height: 7, borderRadius: '50%',
        background: dotColor,
        marginTop: 4, flexShrink: 0,
      }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {isStep && (
          <>
            <div style={{ fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--tp)' }}>
              <span style={{ color: 'rgba(96,165,250,.9)', marginRight: 6 }}>#{evt.step_number}</span>
              {evt.description}
            </div>
            {evt.duration_ms != null && (
              <div style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)', marginTop: 2 }}>
                {evt.duration_ms}ms · {evt.step_type}
              </div>
            )}
          </>
        )}
        {isLlm && (
          <>
            <div style={{ fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--tp)' }}>
              <span style={{ color: 'var(--warn)', marginRight: 6 }}>{evt.model}</span>
            </div>
            <div style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)', marginTop: 2 }}>
              {fmtTok(evt.input_tokens ?? 0)} in · {fmtTok(evt.output_tokens ?? 0)} out · ${(evt.cost_usd ?? 0).toFixed(4)} · {evt.duration_ms}ms
            </div>
          </>
        )}
        {evt.type === 'tool_call' && (
          <div style={{ fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--tp)', display: 'flex', alignItems: 'center', gap: 6 }}>
            {evt.tool_name}
            <span style={{ fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)' }}>· {evt.action}</span>
            <span className={evt.success ? 'badge-pill badge-pill--done' : 'badge-pill badge-pill--err'} style={{ fontSize: 10 }}>
              {evt.success ? 'OK' : 'ERR'}
            </span>
          </div>
        )}
      </div>
      <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)', flexShrink: 0, marginTop: 2 }}>
        {new Date(evt.timestamp).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </span>
    </div>
  )
}

function LlmCard({ call, maxTokens }: { call: TimelineEntry; maxTokens: number }) {
  const inputPct = Math.round(((call.input_tokens ?? 0) / maxTokens) * 100)
  const outputPct = Math.round(((call.output_tokens ?? 0) / maxTokens) * 100)

  return (
    <div className="card" style={{ padding: '12px 14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{
          fontFamily: 'var(--fd)', fontSize: 12,
          padding: '2px 8px', borderRadius: 4,
          background: 'var(--s3)', color: 'var(--warn)',
        }}>
          {call.model}
        </span>
        <span style={{ fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--warn)', fontWeight: 500 }}>
          ${(call.cost_usd ?? 0).toFixed(4)}
        </span>
      </div>
      {/* Token bars */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)', width: 32 }}>IN</span>
          <div style={{ flex: 1, height: 4, background: 'var(--b0)', borderRadius: 99, overflow: 'hidden' }}>
            <div style={{ width: `${inputPct}%`, height: '100%', borderRadius: 99, background: 'linear-gradient(90deg,var(--accent),rgba(45,232,106,.35))', transition: 'width .6s var(--e-out)' }} />
          </div>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tm)', width: 40, textAlign: 'right' }}>{fmtTok(call.input_tokens ?? 0)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tf)', width: 32 }}>OUT</span>
          <div style={{ flex: 1, height: 4, background: 'var(--b0)', borderRadius: 99, overflow: 'hidden' }}>
            <div style={{ width: `${outputPct}%`, height: '100%', borderRadius: 99, background: 'linear-gradient(90deg,var(--warn),rgba(240,180,41,.35))', transition: 'width .6s var(--e-out)' }} />
          </div>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tm)', width: 40, textAlign: 'right' }}>{fmtTok(call.output_tokens ?? 0)}</span>
        </div>
      </div>
      <div style={{ marginTop: 6, fontFamily: 'var(--fd)', fontSize: 12, color: 'var(--tf)' }}>
        {call.duration_ms}ms
      </div>
    </div>
  )
}
