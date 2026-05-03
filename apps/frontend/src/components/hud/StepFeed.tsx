/**
 * StepFeed — HUD panel (bottom-right, w=280px)
 *
 * Feed degli ultimi 6 agent_step con type-tag (LLM/TOOL/THINK) + agent + durata.
 * Dati: store.agentSteps — Record<string, AgentStep[]>
 */

import { useMemo }                 from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useStore }                from '../../store'
import { HudPanel }                from '../ui/HudPanel'
import type { AgentStep }          from '../../types'

/* ── Step type → display + color ─────────────────────────────────────────── */
interface StepTypeMeta { label: string; color: string }

const STEP_TYPE_META: Record<string, StepTypeMeta> = {
  llm:             { label: 'LLM',   color: '#F59E0B' },
  llm_call:        { label: 'LLM',   color: '#F59E0B' },
  tool:            { label: 'TOOL',  color: '#2ECDB7' },
  tool_call:       { label: 'TOOL',  color: '#2ECDB7' },
  think:           { label: 'THINK', color: '#7eb8ff' },
  thinking:        { label: 'THINK', color: '#7eb8ff' },
  capture:         { label: 'CAP',   color: '#8B7CF6' },
  watcher_capture: { label: 'CAP',   color: '#8B7CF6' },
  subagent_spawn:  { label: 'SUB',   color: '#94A3B8' },
  chromadb:        { label: 'MEM',   color: '#94A3B8' },
}

function stepMeta(type: string): StepTypeMeta {
  return STEP_TYPE_META[type.toLowerCase()] ?? { label: type.slice(0, 4).toUpperCase(), color: '#6B7280' }
}

function fmtDuration(ms: number) {
  if (ms <= 0)   return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function fmtAgent(agent: string) {
  return agent.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join('')
}

function flattenSteps(agentSteps: Record<string, AgentStep[]>, limit: number): AgentStep[] {
  const all: AgentStep[] = []
  for (const steps of Object.values(agentSteps)) for (const s of steps) all.push(s)
  all.sort((a, b) => (a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0))
  return all.slice(0, limit)
}

/* ── Skeleton row ────────────────────────────────────────────────────────── */
function SkeletonRow({ delay = 0 }: { delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }}
      transition={{ delay, duration: 0.3 }}
      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
               borderBottom: '1px solid rgba(255,255,255,0.05)' }}
    >
      <div style={{ height: 16, width: 38, borderRadius: 3, background: 'rgba(255,255,255,0.07)', flexShrink: 0 }} />
      <div style={{ height: 9, flex: 1, borderRadius: 3, background: 'rgba(255,255,255,0.05)' }} />
      <div style={{ height: 9, width: 28, borderRadius: 3, background: 'rgba(255,255,255,0.04)', flexShrink: 0 }} />
    </motion.div>
  )
}

/* ── Live step row ───────────────────────────────────────────────────────── */
function StepRow({ step, isLast }: { step: AgentStep; isLast: boolean }) {
  const meta = stepMeta(step.stepType)
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4, transition: { duration: 0.15 } }}
      transition={{ type: 'spring', stiffness: 380, damping: 32 }}
      style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0',
               borderBottom: isLast ? 'none' : '1px solid rgba(255,255,255,0.05)' }}
    >
      <span className="mono-num" style={{
        fontSize: 10, fontWeight: 600,
        color: meta.color, background: `${meta.color}14`,
        padding: '2px 5px', borderRadius: 3, letterSpacing: '0.04em',
        flexShrink: 0, width: 44, textAlign: 'center', display: 'inline-block',
      }}>
        {meta.label}
      </span>
      <span className="mono-num" style={{
        fontSize: 11, color: 'rgba(255,255,255,0.65)',
        flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        letterSpacing: '0.02em',
      }}>
        {fmtAgent(step.agent)}
      </span>
      <span className="mono-num" style={{
        fontSize: 10, color: 'rgba(255,255,255,0.30)',
        flexShrink: 0, minWidth: 32, textAlign: 'right', letterSpacing: '0.01em',
      }}>
        {fmtDuration(step.durationMs)}
      </span>
    </motion.div>
  )
}

/* ── StepFeed ────────────────────────────────────────────────────────────── */
export function StepFeed() {
  const wsConnected = useStore(s => s.wsConnected)
  const agentSteps  = useStore(s => s.agentSteps)

  const steps = useMemo(() => flattenSteps(agentSteps, 6), [agentSteps])

  return (
    <HudPanel title="STEP FEED" style={{ bottom: 16, right: 16, width: 280, zIndex: 10 }}>
      {!wsConnected ? (
        <div>
          <SkeletonRow delay={0}    />
          <SkeletonRow delay={0.05} />
          <SkeletonRow delay={0.10} />
          <SkeletonRow delay={0.15} />
        </div>
      ) : steps.length === 0 ? (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4 }}
          className="mono-num"
          style={{ fontSize: 11, color: 'rgba(255,255,255,0.22)', padding: '8px 0',
                   textAlign: 'center', letterSpacing: '0.04em' }}>
          — nessun step recente —
        </motion.div>
      ) : (
        <AnimatePresence initial={false} mode="popLayout">
          {steps.map((step, i) => (
            <StepRow key={step.id} step={step} isLast={i === steps.length - 1} />
          ))}
        </AnimatePresence>
      )}
    </HudPanel>
  )
}
