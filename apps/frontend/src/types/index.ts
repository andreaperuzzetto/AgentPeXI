/* ── WebSocket event types (server → client) ── */

export interface PepeMessage {
  type: 'pepe_message'
  content: string
  timestamp: string
}

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

export type WSIncoming =
  | PepeMessage
  | AgentStarted
  | AgentCompleted
  | AgentError
  | SystemStatus
  | ToolCallEvent
  | AgentStepEvent
  | LlmCallEvent
  | ContextUpdateEvent

/* ── Client → server ── */

export interface UserMessage {
  type: 'user_message'
  content: string
  session_id: string
}

/* ── Sessions ── */

export interface Session {
  session_id: string
  last_message: string
  timestamp: string
}

/* ── UI models ── */

export interface ChatMessage {
  id: string
  role: 'user' | 'pepe' | 'system'
  content: string
  timestamp: string
  isNew?: boolean
}

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
}

/* ── Cost breakdown (from /api/costs) ── */

export interface CostsBreakdown {
  per_agent: Record<string, number>
  per_tool: Record<string, number>
  per_day: Record<string, number>
  total: number
  budget_threshold_eur: number
}
