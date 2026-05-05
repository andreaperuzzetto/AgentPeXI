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

export interface WarmupProgressEvent {
  type: 'warmup_progress'
  section: string
  analyzed: number
  total: number
}

export interface WarmupCompletedEvent {
  type: 'warmup_completed'
  candidates_count: number
}

export interface SectionMappedEvent {
  type: 'section_mapped'
  niche_key: string
  section_id: string
}

export interface ApprovalFlowEvent {
  type: 'approval_flow'
  item_id: number
  action: 'sent' | 'approved' | 'rejected'
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
  | WarmupProgressEvent
  | WarmupCompletedEvent
  | SectionMappedEvent
  | ApprovalFlowEvent

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

/* ── Niche intelligence (from /api/etsy/niches) ── */

export interface NicheItem {
  niche:               string
  product_type:        string | null
  /* niche_intelligence */
  performance_score:   number
  confidence_level:    'low' | 'medium' | 'high' | string
  avg_ctr:             number | null
  total_orders:        number | null
  total_listings:      number | null
  total_revenue_eur:   number | null
  last_updated_at:     number | null    // unix timestamp
  /* market_signals — may be null if no signal collected yet */
  entry_score:         number | null
  tier:                number | null    // 1 or 2
  avg_price_eur:       number | null
  google_trend_score:  number | null
  /* PA-7 nuovi campi */
  audience_target:     string | null
  expansion_potential: number | null
  section_name:        string | null
}

/* ── Bundle status (from /api/etsy/bundles) ── */

export interface BundleSpec {
  niche:              string
  product_type:       string
  component_titles:   string[]
  component_images:   string[]
  suggested_price:    number
  keywords:           string[]
  entry_score:        number
  n_components:       number
  pod_companion_type: string | null
}

export interface BundleItem {
  niche:      string
  n_listings: number
  score:      number
  spec:       BundleSpec
}

/* ── Production queue (from /api/production-queue) ── */

export type ProductionQueueStatus =
  | 'pending_design'
  | 'pending_approval'
  | 'approved'
  | 'skipped'
  | 'scheduled'
  | 'published'
  | 'failed'
  | 'discarded'
  | 'planned'
  | 'in_progress'
  | 'completed'
  | string   // forward compat

export interface ProductionQueueItem {
  id: number
  task_id: string
  niche: string
  product_type: string
  /** JSON-deserialized by backend */
  brief: Record<string, unknown> | null
  status: ProductionQueueStatus
  entry_score: number | null
  listing_price: number | null
  listing_title: string | null
  /** JSON-deserialized by backend */
  file_paths: string[] | null
  etsy_listing_id: string | null
  ads_activated: number | null
  created_at: string
  updated_at: string
}
