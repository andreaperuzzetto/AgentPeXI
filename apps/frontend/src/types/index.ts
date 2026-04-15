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

export type WSIncoming =
  | PepeMessage
  | AgentStarted
  | AgentCompleted
  | AgentError
  | SystemStatus
  | ToolCallEvent
  | AgentStepEvent

/* ── Client → server ── */

export interface UserMessage {
  type: 'user_message'
  content: string
}

/* ── UI models ── */

export interface ChatMessage {
  id: string
  role: 'user' | 'pepe' | 'system'
  content: string
  timestamp: string
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
  timestamp: string
}

export interface SystemState {
  queueSize: number
  activeTasks: number
  uptime: string
  dailyCost: number
}
