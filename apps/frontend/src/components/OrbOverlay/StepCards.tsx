import { useEffect, useRef, useState } from 'react'
import { useStore } from '../../store'
import type { AgentStep } from '../../types'
import './OrbOverlay.css'

/* ── constants ─────────────────────────────────────────────── */
const MAX_CARDS    = 3
const DISMISS_MS   = 8_000   // inattività prima di fade-out
const FADE_OUT_MS  = 600     // durata animazione sgc-out

function fmtTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function tagType(stepType: string): 'tool' | 'llm' | 'think' {
  if (stepType === 'tool')  return 'tool'
  if (stepType === 'llm')   return 'llm'
  return 'think'
}

/* ── component ─────────────────────────────────────────────── */
export function StepCards({ hidden }: { hidden?: boolean }) {
  const agentSteps = useStore((s) => s.agentSteps)

  /* flattened sorted steps — memoized via ref comparison */
  const [displayed, setDisplayed] = useState<AgentStep[]>([])
  const [fading,    setFading]    = useState(false)

  const dismissTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const fadeTimer    = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastCountRef = useRef(0)

  /* ── watch for new steps ── */
  useEffect(() => {
    const flat: AgentStep[] = Object.values(agentSteps).flat()
    if (flat.length === 0) return

    flat.sort((a, b) => (a.timestamp ? new Date(a.timestamp).getTime() : 0) - (b.timestamp ? new Date(b.timestamp).getTime() : 0))
    const latest = flat.slice(-MAX_CARDS)

    if (flat.length !== lastCountRef.current) {
      lastCountRef.current = flat.length

      /* show cards, cancel any pending dismiss */
      setDisplayed(latest)
      setFading(false)

      if (dismissTimer.current) clearTimeout(dismissTimer.current)
      if (fadeTimer.current)    clearTimeout(fadeTimer.current)

      /* schedule auto-dismiss */
      dismissTimer.current = setTimeout(() => {
        setFading(true)
        fadeTimer.current = setTimeout(() => setDisplayed([]), FADE_OUT_MS)
      }, DISMISS_MS)
    }
  }, [agentSteps])

  /* cleanup on unmount */
  useEffect(() => () => {
    if (dismissTimer.current) clearTimeout(dismissTimer.current)
    if (fadeTimer.current)    clearTimeout(fadeTimer.current)
  }, [])

  if (displayed.length === 0 || hidden) return null

  const total = displayed.length

  return (
    <div className="step-cards-wrap">
      {displayed.map((step, idx) => {
        const tag = tagType(step.stepType)
        /*
         * Stagger sequenziale: la card più recente (in basso con column-reverse)
         * ha indice più alto → appare subito (delay 0).
         * Le card più vecchie (sopra) appaiono con ritardo crescente.
         * offset = (total - 1 - idx) in ordine cronologico ascendente.
         */
        const staggerDelay = fading ? 0 : (total - 1 - idx) * 130
        return (
          <div
            key={step.id}
            className={`step-glass-card${fading ? ' fading' : ''}`}
            style={{ animationDelay: `${staggerDelay}ms` }}
          >
            {/* type badge */}
            <span className={`sgc-tag ${tag}`}>{tag}</span>

            {/* body */}
            <div className="sgc-body">
              <div className="sgc-agent">{step.agent}</div>
              <div className="sgc-desc">{step.description}</div>
            </div>

            {/* time */}
            <span className="sgc-time">{fmtTime(step.timestamp)}</span>
          </div>
        )
      })}
    </div>
  )
}
