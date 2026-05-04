/**
 * SystemView — FE-Blocco 6
 *
 * Layout two-column:
 *   LEFT  (1fr)  — [ BUSINESS AGENTS ] grid-cols-2 + [ SERVICES ] compact list
 *   RIGHT (340px) — [ PERSONAL ] grid-cols-2 + [ JOBS ] scheduler + [ CONFIG ]
 *
 * Densità 7/10 — padding card 12px, gap sezioni 12px.
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useStore } from '../store'
import { GlassCard } from '../components/ui/GlassCard'
import { AgentCard, AGENT_MODELS } from '../components/AgentCard/AgentCard'
import type { AgentCardProps, AgentLayer } from '../components/AgentCard/AgentCard'
import type { AgentStep } from '../types'

// ── Agent / service definitions ────────────────────────────────────────────────

interface AgentDef {
  name:             string
  layer:            AgentLayer
  isService?:       boolean
  pipelinePos?:     string
}

const BUSINESS_AGENTS: AgentDef[] = [
  { name: 'research',    layer: 'business', pipelinePos: '1 · research' },
  { name: 'design',      layer: 'business', pipelinePos: '2 · design' },
  { name: 'publisher',   layer: 'business', pipelinePos: '3 · publisher' },
  { name: 'analytics',   layer: 'business' },
  { name: 'finance',     layer: 'business' },
  { name: 'market_data', layer: 'business' },
]

const SERVICE_AGENTS: AgentDef[] = [
  { name: 'autopilot_loop',   layer: 'service', isService: true },
  { name: 'learning_loop',    layer: 'service', isService: true },
  { name: 'bundle_strategy',  layer: 'service', isService: true },
  { name: 'etsy_ads_manager', layer: 'service', isService: true },
  { name: 'shop_optimizer',   layer: 'service', isService: true },
  { name: 'finance_tracker',  layer: 'service', isService: true },
]

const PERSONAL_AGENTS: AgentDef[] = [
  { name: 'recall',            layer: 'personal' },
  { name: 'remind',            layer: 'personal' },
  { name: 'summarize',         layer: 'personal' },
  { name: 'research_personal', layer: 'personal' },
  { name: 'watcher',           layer: 'personal' },
]

// Stable empty array so Zustand selectors don't return a new reference on every call
const EMPTY_STEPS: AgentStep[] = []


const SERVICE_STATUS_OVERRIDES = new Set([
  'autopilot_loop', 'learning_loop', 'bundle_strategy',
  'etsy_ads_manager', 'shop_optimizer', 'finance_tracker',
])

const SPRING_ENTRY = (delay: number) => ({
  initial:    { opacity: 0, y: 10 } as const,
  animate:    { opacity: 1, y:  0 } as const,
  transition: { type: 'spring' as const, stiffness: 260, damping: 30, delay },
})

// ── Helpers ────────────────────────────────────────────────────────────────────

function todayStart(): Date {
  const d = new Date(); d.setHours(0, 0, 0, 0); return d
}

function useAgentCard(def: AgentDef) {
  const agent      = useStore((s) => s.agents[def.name])
  const agentSteps = useStore((s) => s.agentSteps[def.name])
  const autopilotStatus = useStore((s) => s.autopilotStatus)

  let status: AgentCardProps['status'] = 'idle'
  if (SERVICE_STATUS_OVERRIDES.has(def.name)) {
    if (def.name === 'autopilot_loop') {
      status = autopilotStatus === 'running' ? 'running' : 'idle'
    }
  } else {
    status = agent?.status === 'running' ? 'running' :
             agent?.status === 'error'   ? 'error'   : 'idle'
  }

  const t = todayStart()
  const stepsToday = agentSteps?.filter((s) => new Date(s.timestamp) >= t).length ?? 0

  return {
    status,
    lastTask:  agent?.lastTask,
    stepsToday,
  }
}

// ── AgentCardConnected ─────────────────────────────────────────────────────────
// Thin wrapper that connects a def to the store and renders AgentCard

function AgentCardConnected({ def, onSelect }: { def: AgentDef; onSelect: (d: AgentDef) => void }) {
  const { status, lastTask, stepsToday } = useAgentCard(def)
  return (
    <AgentCard
      name={def.name}
      layer={def.layer}
      status={status}
      model={def.isService ? undefined : AGENT_MODELS[def.name]}
      lastTask={lastTask}
      stepsToday={stepsToday}
      pipelinePosition={def.pipelinePos}
      isService={def.isService}
      onClick={() => onSelect(def)}
    />
  )
}

// ── ServiceRow — compact list item for services ────────────────────────────────

function ServiceRow({ def, onSelect }: { def: AgentDef; onSelect: (d: AgentDef) => void }) {
  const { status, stepsToday } = useAgentCard(def)
  const [hovered, setHovered] = useState(false)
  const dotColor = status === 'running'
    ? 'rgba(27,255,94,0.85)'
    : status === 'error'
      ? 'rgba(255,68,68,0.85)'
      : 'var(--tf)'
  const label = def.name.replace(/_/g, ' ')

  return (
    <div
      onClick={() => onSelect(def)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display:      'flex',
        alignItems:   'center',
        gap:          8,
        padding:      '5px 10px',
        borderRadius: 5,
        background:   hovered ? 'rgba(255,255,255,0.03)' : 'rgba(255,255,255,0.01)',
        border:       `1px solid ${hovered ? 'rgba(255,255,255,0.09)' : 'rgba(255,255,255,0.04)'}`,
        cursor:       'pointer',
        transition:   'background 0.12s ease, border-color 0.12s ease',
      }}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', background: dotColor, flexShrink: 0 }} />
      <span style={{
        fontFamily:    'var(--fmo)',
        fontSize:      11,
        color:         'var(--tm)',
        flex:          1,
        textTransform: 'capitalize',
      }}>
        {label}
      </span>
      <span style={{
        fontFamily:    'var(--fmo)',
        fontSize:      10,
        letterSpacing: '0.08em',
        textTransform: 'uppercase',
        padding:       '1px 5px',
        borderRadius:  3,
        background:    'rgba(107,114,128,0.12)',
        color:         'var(--zone-system)',
        border:        '1px solid rgba(107,114,128,0.16)',
      }}>
        SVC
      </span>
      <span style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)', minWidth: 52, textAlign: 'right' }}
            className="mono-num">
        {stepsToday} step
      </span>
    </div>
  )
}

// ── AgentDetailModal ───────────────────────────────────────────────────────────

const STEP_TYPE_COLOR: Record<string, string> = {
  llm_call:   'rgba(94,234,212,0.85)',
  tool_call:  'rgba(251,191,36,0.85)',
  plan:       'rgba(167,139,250,0.85)',
  think:      'rgba(167,139,250,0.85)',
  search:     'rgba(96,165,250,0.85)',
  memory:     'rgba(52,211,153,0.85)',
}
function stepTypeColor(t: string): string {
  return STEP_TYPE_COLOR[t.toLowerCase()] ?? 'var(--tf)'
}

function formatRelTime(iso: string): string {
  try {
    const diff = Date.now() - new Date(iso).getTime()
    if (diff < 5_000)  return 'adesso'
    if (diff < 60_000) return `${Math.floor(diff / 1000)}s fa`
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m fa`
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h fa`
    return new Date(iso).toLocaleDateString('it-IT', { day: '2-digit', month: 'short' })
  } catch { return '—' }
}

function fmtDuration(ms: number): string {
  if (!ms || ms <= 0) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function AgentDetailModal({ def, onClose }: { def: AgentDef; onClose: () => void }) {
  const agent        = useStore((s) => s.agents[def.name])
  const addAgentStep = useStore((s) => s.addAgentStep)
  // Must NOT use `?? []` inside the selector — see comment in SystemView
  const rawSteps = useStore((s) => s.agentSteps[def.name])
  const allSteps = rawSteps ?? EMPTY_STEPS
  const autopilotStatus = useStore((s) => s.autopilotStatus)
  const panelRef  = useRef<HTMLDivElement>(null)
  const fetchedRef = useRef(false)
  const [loading, setLoading] = useState(false)

  // ── Rehydrate steps from backend on open ──────────────────────────────────
  useEffect(() => {
    if (fetchedRef.current) return
    fetchedRef.current = true
    setLoading(true)
    fetch(`/api/agents/steps/recent?limit=100&agent_name=${encodeURIComponent(def.name)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!Array.isArray(data?.steps)) return
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
            durationMs:  s.duration_ms  ?? 0,
            timestamp:   s.timestamp,
          })
        })
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [def.name, addAgentStep])

  const today      = useMemo(() => todayStart(), [])
  const stepsToday = allSteps.filter((s) => new Date(s.timestamp) >= today).length
  const sorted     = useMemo(
    () => [...allSteps].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()),
    [allSteps]
  )

  let status: AgentCardProps['status'] = 'idle'
  if (SERVICE_STATUS_OVERRIDES.has(def.name)) {
    if (def.name === 'autopilot_loop') status = autopilotStatus === 'running' ? 'running' : 'idle'
  } else {
    status = agent?.status === 'running' ? 'running' :
             agent?.status === 'error'   ? 'error'   : 'idle'
  }

  const accentColor = def.layer === 'business'
    ? 'var(--zone-etsy)' : def.layer === 'personal'
    ? 'var(--zone-personal)' : 'var(--zone-system)'

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  return (
    <>
      {/* Backdrop */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.18 }}
        onClick={onClose}
        style={{
          position:       'fixed',
          inset:          0,
          background:     'rgba(0,0,0,0.55)',
          backdropFilter: 'blur(3px)',
          zIndex:         1000,
        }}
      />

      {/* Panel — slides in from right */}
      <motion.div
        ref={panelRef}
        initial={{ x: 48, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: 48, opacity: 0 }}
        transition={{ type: 'spring', stiffness: 300, damping: 32 }}
        style={{
          position:    'fixed',
          right:       0,
          top:         0,
          bottom:      0,
          width:       440,
          maxWidth:    '92vw',
          background:  'rgba(10,12,15,0.97)',
          borderLeft:  '1px solid rgba(255,255,255,0.09)',
          boxShadow:   '-24px 0 80px rgba(0,0,0,0.4)',
          zIndex:      1001,
          display:     'flex',
          flexDirection: 'column',
          overflowY:   'hidden',
        }}
      >
        {/* ── Panel header ──────────────────────────────────────── */}
        <div style={{
          padding:      '16px 20px 14px',
          borderBottom: '1px solid rgba(255,255,255,0.07)',
          display:      'flex',
          alignItems:   'center',
          gap:          10,
          flexShrink:   0,
        }}>
          {/* Status dot */}
          <span style={{
            width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
            background: status === 'running' ? 'rgba(27,255,94,0.85)'
              : status === 'error' ? 'rgba(255,68,68,0.85)' : 'var(--tf)',
            boxShadow: status === 'running' ? '0 0 8px rgba(27,255,94,0.5)' : 'none',
          }} />

          {/* Name */}
          <span style={{
            fontFamily:    'var(--fui)',
            fontSize:      15,
            fontWeight:    600,
            color:         accentColor,
            textTransform: 'capitalize',
            flex:          1,
          }}>
            {def.name.replace(/_/g, ' ')}
          </span>

          {/* Status badge */}
          <span style={{
            fontFamily:    'var(--fmo)',
            fontSize:      10,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            padding:       '2px 7px',
            borderRadius:  3,
            ...(status === 'running'
              ? { background: 'rgba(27,255,94,0.12)', color: 'rgba(27,255,94,0.85)', border: '1px solid rgba(27,255,94,0.20)' }
              : status === 'error'
              ? { background: 'rgba(255,68,68,0.12)', color: 'rgba(255,68,68,0.85)', border: '1px solid rgba(255,68,68,0.20)' }
              : { background: 'rgba(255,255,255,0.05)', color: 'var(--tf)', border: '1px solid rgba(255,255,255,0.06)' }),
          }}>
            {status}
          </span>

          {/* Close */}
          <button
            onClick={onClose}
            style={{
              background:  'rgba(255,255,255,0.06)',
              border:      '1px solid rgba(255,255,255,0.08)',
              borderRadius: 5,
              color:       'var(--tm)',
              fontFamily:  'var(--fmo)',
              fontSize:    12,
              width:       28,
              height:      28,
              display:     'flex',
              alignItems:  'center',
              justifyContent: 'center',
              cursor:      'pointer',
              flexShrink:  0,
            }}
          >
            ×
          </button>
        </div>

        {/* ── Meta row ──────────────────────────────────────────── */}
        <div style={{
          padding:      '10px 20px',
          borderBottom: '1px solid rgba(255,255,255,0.05)',
          display:      'flex',
          gap:          10,
          flexWrap:     'wrap',
          flexShrink:   0,
        }}>
          {/* Layer badge */}
          <span style={{
            fontFamily:    'var(--fmo)',
            fontSize:      10,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            padding:       '2px 7px',
            borderRadius:  3,
            background:    'rgba(255,255,255,0.05)',
            color:         accentColor,
            border:        '1px solid rgba(255,255,255,0.07)',
          }}>
            {def.layer}
          </span>

          {/* Model */}
          {!def.isService && AGENT_MODELS[def.name] && (
            <span style={{
              fontFamily:    'var(--fmo)',
              fontSize:      10,
              letterSpacing: '0.06em',
              padding:       '2px 7px',
              borderRadius:  3,
              background:    'rgba(255,255,255,0.03)',
              color:         'var(--tf)',
              border:        '1px solid rgba(255,255,255,0.05)',
            }}>
              {AGENT_MODELS[def.name]}
            </span>
          )}

          {/* Pipeline */}
          {def.pipelinePos && (
            <span style={{
              fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)',
              padding: '2px 7px', borderRadius: 3,
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(255,255,255,0.05)',
            }}>
              {def.pipelinePos}
            </span>
          )}

          {/* Steps today */}
          <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 5 }}>
            <span className="mono-num" style={{ fontFamily: 'var(--fmo)', fontSize: 18, fontWeight: 600, color: 'var(--tp)' }}>
              {stepsToday}
            </span>
            <span style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              step oggi
            </span>
          </span>
        </div>

        {/* ── Steps log ─────────────────────────────────────────── */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 0' }}>
          <div style={{ padding: '0 20px 6px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span className="hud-label">[ STEPS LOG ]</span>
            <span className="mono-num" style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)' }}>
              {sorted.length} totali
            </span>
          </div>

          {loading ? (
            <div style={{
              padding: '24px 20px', fontFamily: 'var(--fmo)',
              fontSize: 11, color: 'var(--tf)', textAlign: 'center', opacity: 0.5,
            }}>
              Caricamento…
            </div>
          ) : sorted.length === 0 ? (
            <div style={{
              padding:    '24px 20px',
              fontFamily: 'var(--fmo)',
              fontSize:   11,
              color:      'var(--tf)',
              textAlign:  'center',
              opacity:    0.5,
            }}>
              Nessuno step registrato
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {sorted.map((step, i) => (
                <StepRow key={step.id ?? i} step={step} />
              ))}
            </div>
          )}
        </div>
      </motion.div>
    </>
  )
}

function StepRow({ step }: { step: AgentStep }) {
  const typeColor = stepTypeColor(step.stepType)

  return (
    <div style={{
      padding:      '8px 20px',
      borderBottom: '1px solid rgba(255,255,255,0.03)',
      display:      'grid',
      gridTemplateColumns: '28px 1fr auto',
      gridTemplateRows: 'auto auto',
      gap:          '2px 8px',
    }}>
      {/* Step number */}
      <span
        className="mono-num"
        style={{
          fontFamily:  'var(--fmo)',
          fontSize:    10,
          color:       'var(--tf)',
          opacity:     0.6,
          gridRow:     '1 / 3',
          alignSelf:   'center',
          textAlign:   'right',
        }}
      >
        #{step.stepNumber}
      </span>

      {/* Description */}
      <span style={{
        fontFamily:  'var(--fmo)',
        fontSize:    11,
        color:       'var(--tm)',
        lineHeight:  1.45,
        gridColumn:  2,
        gridRow:     1,
        overflow:    'hidden',
        display:     '-webkit-box',
        WebkitLineClamp: 2,
        WebkitBoxOrient: 'vertical',
      }}>
        {step.description || <span style={{ opacity: 0.4 }}>—</span>}
      </span>

      {/* Timestamp */}
      <span
        className="mono-num"
        style={{
          fontFamily: 'var(--fmo)',
          fontSize:   10,
          color:      'var(--tf)',
          gridColumn: 3,
          gridRow:    1,
          whiteSpace: 'nowrap',
          alignSelf:  'start',
        }}
      >
        {formatRelTime(step.timestamp)}
      </span>

      {/* Type badge + duration */}
      <div style={{ gridColumn: 2, gridRow: 2, display: 'flex', gap: 6, alignItems: 'center' }}>
        <span style={{
          fontFamily:    'var(--fmo)',
          fontSize:      9,
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
          color:         typeColor,
          padding:       '1px 4px',
          borderRadius:  2,
          background:    typeColor.replace('0.85', '0.1'),
          border:        `1px solid ${typeColor.replace('0.85', '0.2')}`,
        }}>
          {step.stepType}
        </span>
        <span className="mono-num" style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)' }}>
          {fmtDuration(step.durationMs)}
        </span>
      </div>
    </div>
  )
}

// ── Scheduler Jobs Table ───────────────────────────────────────────────────────

interface SchedulerJob {
  id:        string
  name:      string
  trigger:   string
  next_run:  string | null
  last_run:  string | null
  status?:   string
}

function formatJobTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d   = new Date(iso)
    if (isNaN(d.getTime())) return iso
    const now = new Date()
    const diff = d.getTime() - now.getTime()
    if (Math.abs(diff) < 60_000) return 'ora'
    if (diff > 0) {
      const h = Math.round(diff / 3_600_000)
      const m = Math.round(diff / 60_000)
      if (h < 24) return m < 60 ? `tra ${m}m` : `tra ${h}h`
    }
    return d.toLocaleString('it-IT', {
      weekday: 'short', hour: '2-digit', minute: '2-digit',
    })
  } catch { return iso ?? '—' }
}

function SchedulerJobsPanel() {
  const [jobs, setJobs] = useState<SchedulerJob[]>([])

  const fetchJobs = useCallback(() => {
    fetch('/api/scheduler/jobs')
      .then((r) => r.ok ? r.json() : { jobs: [] })
      .then((d) => setJobs(Array.isArray(d.jobs) ? d.jobs : []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchJobs()
    const id = setInterval(fetchJobs, 30_000)
    return () => clearInterval(id)
  }, [fetchJobs])

  return (
    <GlassCard hudVariant innerStyle={{ padding: 12, overflow: 'hidden' }}>
      {/* Header */}
      <span className="hud-label" style={{ display: 'block', marginBottom: 10 }}>
        [ JOBS ]
      </span>

      {jobs.length === 0 ? (
        <div style={{ fontFamily: 'var(--fmo)', fontSize: 11, color: 'var(--tf)', padding: '6px 0' }}>
          Nessun job schedulato
        </div>
      ) : (
        <div style={{ overflow: 'auto', maxHeight: 220 }}>
          {/* Column headers */}
          <div style={{
            display:    'grid',
            gridTemplateColumns: '1fr 100px 110px 110px',
            gap:        6,
            padding:    '3px 4px 5px',
            borderBottom: '1px solid rgba(255,255,255,0.06)',
            marginBottom: 2,
          }}>
            {['Nome job', 'Trigger', 'Next run', 'Last run'].map((h) => (
              <span key={h} className="hud-label" style={{ fontSize: 9 }}>{h}</span>
            ))}
          </div>

          {/* Rows */}
          {jobs.map((job, i) => (
            <div
              key={job.id}
              style={{
                display:    'grid',
                gridTemplateColumns: '1fr 100px 110px 110px',
                gap:        6,
                padding:    '4px 4px',
                background: i % 2 === 0 ? 'rgba(255,255,255,0.01)' : 'transparent',
                borderRadius: 3,
              }}
            >
              <span style={{
                fontFamily:   'var(--fmo)',
                fontSize:     11,
                color:        'var(--tm)',
                overflow:     'hidden',
                textOverflow: 'ellipsis',
                whiteSpace:   'nowrap',
              }}>
                {job.name}
              </span>
              <span className="mono-num" style={{
                fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {job.trigger || '—'}
              </span>
              <span className="mono-num" style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tm)' }}>
                {formatJobTime(job.next_run)}
              </span>
              <span className="mono-num" style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)' }}>
                {formatJobTime(job.last_run)}
              </span>
            </div>
          ))}
        </div>
      )}
    </GlassCard>
  )
}

// ── Config Live Panel ──────────────────────────────────────────────────────────

function ConfigPanel() {
  const [mockMode,   setMockMode]   = useState<boolean | null>(null)
  const [autopilot,  setAutopilot]  = useState<{ status: string; items_today: number } | null>(null)
  const budgetMax = useStore((s) => s.budgetMonthlyUsd)

  const refresh = useCallback(() => {
    fetch('/api/mock/status')
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setMockMode(d.mock_mode) })
      .catch(() => {})

    fetch('/api/autopilot/status')
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setAutopilot({ status: d.status ?? '—', items_today: d.items_today ?? 0 }) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30_000)
    return () => clearInterval(id)
  }, [refresh])

  const configRows: Array<{ label: string; value: string }> = [
    {
      label: 'Mock mode',
      value: mockMode === null ? '…' : mockMode ? 'ON' : 'OFF',
    },
    {
      label: 'Budget soglia',
      value: budgetMax ? `$${budgetMax.toFixed(0)}/mese` : '—',
    },
    {
      label: 'Autopilot',
      value: autopilot
        ? `${autopilot.status} · ${autopilot.items_today} oggi`
        : '…',
    },
  ]

  return (
    <GlassCard hudVariant innerStyle={{ padding: 12 }}>
      <span className="hud-label" style={{ display: 'block', marginBottom: 10 }}>
        [ CONFIG ]
      </span>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {configRows.map((row) => (
          <div key={row.label} style={{
            display:      'flex',
            alignItems:   'center',
            justifyContent: 'space-between',
            padding:      '4px 8px',
            background:   'rgba(255,255,255,0.015)',
            border:       '1px solid rgba(255,255,255,0.04)',
            borderRadius: 4,
          }}>
            <span style={{ fontFamily: 'var(--fmo)', fontSize: 10, color: 'var(--tf)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              {row.label}
            </span>
            <span className="mono-num" style={{ fontFamily: 'var(--fmo)', fontSize: 11, color: 'var(--tm)' }}>
              {row.value}
            </span>
          </div>
        ))}
      </div>
    </GlassCard>
  )
}

// ── SystemView ─────────────────────────────────────────────────────────────────

export function SystemView() {
  const setAgentStatus     = useStore((s) => s.setAgentStatus)
  const setAutopilotStatus = useStore((s) => s.setAutopilotStatus)
  const [selectedDef, setSelectedDef] = useState<AgentDef | null>(null)
  const handleClose = useCallback(() => setSelectedDef(null), [])

  useEffect(() => {
    const fetchAgents = () =>
      fetch('/api/agents')
        .then((r) => r.ok ? r.json() : null)
        .then((d) => {
          if (!d?.agents) return
          Object.entries(d.agents as Record<string, string>).forEach(([name, status]) => {
            setAgentStatus(name, status as 'idle' | 'running' | 'error')
          })
        })
        .catch(() => {})

    const fetchAutopilot = () =>
      fetch('/api/autopilot/status')
        .then((r) => r.ok ? r.json() : null)
        .then((d) => {
          if (d?.status) setAutopilotStatus(d.status, d.current_niche ?? null)
        })
        .catch(() => {})

    fetchAgents(); fetchAutopilot()
    const id1 = setInterval(fetchAgents, 15_000)
    const id2 = setInterval(fetchAutopilot, 30_000)
    return () => { clearInterval(id1); clearInterval(id2) }
  }, [setAgentStatus, setAutopilotStatus])

  return (
    <>
      <style>{`
        .sys-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 16px;
          align-items: start;
          width: 100%;
        }
        .sys-agents-grid-2 {
          display: grid;
          grid-template-columns: 1fr 1fr;
          grid-auto-rows: 145px;
          gap: 10px;
        }
        @media (max-width: 900px) {
          .sys-grid { grid-template-columns: 1fr; }
        }
        @media (max-width: 600px) {
          .sys-agents-grid-2 { grid-template-columns: 1fr; }
        }
      `}</style>

      <div style={{
        width:     '100%',
        height:    '100%',
        padding:   20,
        overflowY: 'auto',
        overflowX: 'hidden',
        boxSizing: 'border-box',
      }}>
        <div className="sys-grid">

          {/* ── LEFT: Business + Services ─────────────────────────── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

            {/* Business agents */}
            <motion.section {...SPRING_ENTRY(0.04)} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <span className="hud-label">[ BUSINESS AGENTS ]</span>
              <div className="sys-agents-grid-2">
                {BUSINESS_AGENTS.map((def) => (
                  <AgentCardConnected key={def.name} def={def} onSelect={setSelectedDef} />
                ))}
              </div>
            </motion.section>

            {/* Services */}
            <motion.section {...SPRING_ENTRY(0.08)} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <span className="hud-label">[ SERVICES ]</span>
              <GlassCard hudVariant innerStyle={{ padding: '8px 4px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {SERVICE_AGENTS.map((def) => (
                    <ServiceRow key={def.name} def={def} onSelect={setSelectedDef} />
                  ))}
                </div>
              </GlassCard>
            </motion.section>
          </div>

          {/* ── RIGHT: Personal + Jobs + Config ───────────────────── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, position: 'sticky', top: 0 }}>

            {/* Personal agents */}
            <motion.section {...SPRING_ENTRY(0.06)} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <span className="hud-label">[ PERSONAL ]</span>
              <div className="sys-agents-grid-2">
                {PERSONAL_AGENTS.map((def) => (
                  <AgentCardConnected key={def.name} def={def} onSelect={setSelectedDef} />
                ))}
              </div>
            </motion.section>

            {/* Scheduler jobs */}
            <motion.section {...SPRING_ENTRY(0.10)}>
              <SchedulerJobsPanel />
            </motion.section>

            {/* Config live */}
            <motion.section {...SPRING_ENTRY(0.14)}>
              <ConfigPanel />
            </motion.section>
          </div>
        </div>
      </div>

      {/* ── Agent Detail Modal ────────────────────────────────────── */}
      <AnimatePresence>
        {selectedDef && (
          <AgentDetailModal
            def={selectedDef}
            onClose={handleClose}
          />
        )}
      </AnimatePresence>
    </>
  )
}
