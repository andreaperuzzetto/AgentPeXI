/** Static map: agent name → LLM model label */
export const AGENT_MODELS: Record<string, string> = {
  // Business
  research:          'claude-sonnet',
  design:            'claude-sonnet',
  publisher:         'claude-sonnet',
  analytics:         'claude-sonnet',
  finance:           'claude-sonnet',
  market_data:       'claude-haiku',
  // Personal
  recall:            'claude-haiku',
  remind:            'claude-haiku',
  summarize:         'claude-haiku',
  research_personal: 'claude-sonnet',
  watcher:           'ollama',
}
