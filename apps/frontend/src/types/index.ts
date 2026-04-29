/* ── WebSocket event types (server → client) ── */

export interface AgentStarted {
  type: 'agent_started'
  agent: string
  task_id: string
  description?: string
}

export interface AgentCompleted {
  type: 'agent_completed'
  agent: string
  task_id: string
  duration_ms: number
}

export interface AgentError {
  type: 'agent_error'
  agent: string
  task_id: string
  error: string
}

export interface SystemStatus {
  type: 'system_status'
  queue_size: number
  active_tasks: number
  mock_mode?: boolean
  /** Autopilot fields — opzionali, inviati dal backend quando disponibili */
  autopilot_status?: 'running' | 'paused' | 'stopped'
  autopilot_niche?:  string | null
}

export interface ToolCallEvent {
  type: 'tool_call'
  agent: string
  task_id: string
  tool: string
  action: string
  status: 'success' | 'error'
  duration_ms: number
  cost_usd: number | null
  timestamp: string
}

export interface AgentStepEvent {
  type: 'agent_step'
  agent: string
  task_id: string
  step_id: string
  step_number: number
  step_type: string
  description: string
  duration_ms: number
  timestamp: string
}

export interface LlmCallEvent {
  type: 'llm_call'
  agent: string
  task_id: string
  step_id: string
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
  duration_ms: number
}

export interface ContextUpdateEvent {
  type: 'context_update'
  confidence_threshold: number
  confidence_current: number | null
  strategy: string
  domain: string
  next_action: string
  retry_policy: string
  failure_count: number
  trigger: string
  timestamp: string
}

export interface WatcherStatus {
  type: 'watcher_status'
  status: 'active' | 'paused' | 'error'
  message?: string
  last_task?: string
  captures_today: number
  last_capture_time: string | null
  last_capture_app: string | null
}

export interface WatcherCapture {
  type: 'watcher_capture'
  agent: string
  task_id: string
  step_id: string
  step_number: number
  step_type: string
  description: string
  duration_ms: number
  timestamp: string
  app_name: string
  chunks: number
}

export interface DomainSwitched {
  type: 'domain_switched'
  domain: string
}

export interface MemoryQueryEvent {
  type: 'memory_query'
  agent: string
  collection: string
  ids: string[]
  query: string | null
  ts: string
}

export interface KnowledgeBridgeEvent {
  type: 'knowledge_bridge'
  topic: string
  source_etsy: string
  source_personal: string
  ts: number
}

export type WSIncoming =
  | AgentStarted
  | AgentCompleted
  | AgentError
  | SystemStatus
  | ToolCallEvent
  | AgentStepEvent
  | LlmCallEvent
  | ContextUpdateEvent
  | WatcherStatus
  | WatcherCapture
  | DomainSwitched
  | MemoryQueryEvent
  | KnowledgeBridgeEvent

/* ── UI models ── */

export type AgentStatusValue = 'idle' | 'running' | 'error'

export interface AgentState {
  status: AgentStatusValue
  lastTask: string
}

export interface ToolEvent {
  id: string
  agent: string
  tool: string
  action: string
  status: 'success' | 'error'
  duration_ms: number
  cost_usd: number | null
  timestamp: string
}

export interface SystemState {
  queueSize: number
  activeTasks: number
  uptime: string
  dailyCost: number
  mock_mode?: boolean
}

export interface AgentStep {
  id: string
  agent: string
  taskId: string
  stepNumber: number
  stepType: string
  description: string
  durationMs: number
  timestamp: string
}

/* ── Timeline (from /api/tasks/{id}/timeline) ── */

export interface TimelineEntry {
  type: 'agent_step' | 'llm_call' | 'tool_call'
  timestamp: string
  step_number?: number
  step_type?: string
  description?: string
  duration_ms?: number
  model?: string
  input_tokens?: number
  output_tokens?: number
  cost_usd?: number
  tool_name?: string
  action?: string
  success?: boolean
  status?: string
}

/* ── Cost breakdown (from /api/costs) ── */

export interface CostsBreakdown {
  per_agent: Record<string, number>
  per_tool: Record<string, number>
  per_day: Record<string, number>
  total: number
  budget_threshold_eur: number
}
