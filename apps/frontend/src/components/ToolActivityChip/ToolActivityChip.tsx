import { useEffect, useRef, useState } from 'react'
import { useStore } from '../../store'
import type { AgentStep } from '../../types/index'

const HIDE_DELAY = 4200 // ms

function relTime(ts: string): string {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (diff < 5)  return 'adesso'
  if (diff < 60) return `${diff}s fa`
  return `${Math.floor(diff / 60)}m fa`
}

export function ToolActivityChip() {
  const agentSteps = useStore((s) => s.agentSteps)
  const [visible, setVisible]   = useState(false)
  const [current, setCurrent]   = useState<AgentStep | null>(null)
  const timerRef                = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastIdRef               = useRef<string | null>(null)

  // Find the most recent tool step across all agents
  const lastToolStep: AgentStep | null = (() => {
    let latest: AgentStep | null = null
    for (const steps of Object.values(agentSteps)) {
      for (const step of steps) {
        if (step.stepType === 'tool') {
          if (!latest || step.timestamp > latest.timestamp) {
            latest = step
          }
        }
      }
    }
    return latest
  })()

  useEffect(() => {
    if (!lastToolStep) return
    if (lastToolStep.id === lastIdRef.current) return

    lastIdRef.current = lastToolStep.id
    setCurrent(lastToolStep)
    setVisible(true)

    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setVisible(false), HIDE_DELAY)
  }, [lastToolStep])

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current)
  }, [])

  if (!current) return null

  return (
    <div className={`tool-chip${visible ? ' visible' : ''}`}>
      <div className="tc-header">
        <span className="tc-dot" />
        <span className="tc-agent">{current.agent}</span>
        <span className="tc-badge">tool</span>
      </div>
      <div className="tc-desc">{current.description}</div>
      <div className="tc-time">{relTime(current.timestamp)}</div>
    </div>
  )
}
