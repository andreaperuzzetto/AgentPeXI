/**
 * SystemView — exported constants for agent lists and status overrides.
 * Extracted here so they can be tested independently of the React component.
 */

export interface AgentDef {
  name:        string
  layer:       'business' | 'service' | 'personal'
  isService?:  boolean
  pipelinePos?: string
}

export const BUSINESS_AGENTS: AgentDef[] = [
  { name: 'research',    layer: 'business', pipelinePos: '1 · research' },
  { name: 'design',      layer: 'business', pipelinePos: '2 · design' },
  { name: 'publisher',   layer: 'business', pipelinePos: '3 · publisher' },
  { name: 'pinterest',   layer: 'business', pipelinePos: '4 · pinterest' },
  { name: 'analytics',   layer: 'business' },
  { name: 'finance',     layer: 'business' },
  { name: 'market_data', layer: 'business' },
]

export const SERVICE_AGENTS: AgentDef[] = [
  { name: 'autopilot_loop',   layer: 'service', isService: true },
  { name: 'learning_loop',    layer: 'service', isService: true },
  { name: 'bundle_strategy',  layer: 'service', isService: true },
  { name: 'etsy_ads_manager', layer: 'service', isService: true },
  { name: 'shop_optimizer',   layer: 'service', isService: true },
  { name: 'finance_tracker',  layer: 'service', isService: true },
  { name: 'pinterest_oauth',  layer: 'service', isService: true },
]

export const PERSONAL_AGENTS: AgentDef[] = [
  { name: 'recall',            layer: 'personal' },
  { name: 'remind',            layer: 'personal' },
  { name: 'summarize',         layer: 'personal' },
  { name: 'research_personal', layer: 'personal' },
  { name: 'watcher',           layer: 'personal' },
]

export const SERVICE_STATUS_OVERRIDES = new Set([
  'autopilot_loop', 'learning_loop', 'bundle_strategy',
  'etsy_ads_manager', 'shop_optimizer', 'finance_tracker',
  'pinterest_oauth',
])
