import { create } from 'zustand'
import type { AgentState, ToolEvent, SystemState, AgentStep, ContextUpdateEvent } from '../types'

export interface CacheStats {
  /** Token serviti dalla cache (cache_read_tokens) */
  readTokens: number
  /** Token scritti in cache (cache_write_tokens) */
  writeTokens: number
  /** Risparmio in USD rispetto a pagare full input price */
  savingsUsd: number
  /** % dei token input serviti da cache (0–100) */
  efficiencyPct: number
}

export interface LlmStats {
  /** Tokens accumulated in this session from WS llm_call events */
  inputTokens: number
  outputTokens: number
  /** Cost accumulated in this session from WS llm_call events */
  runCost: number
  /** Fetched from /api/costs — total project cost */
  totalCost: number
  /** Fetched from /api/costs — per-agent totals */
  perAgent: Record<string, number>
  /** Fetched from /api/costs — per-day totals (key = YYYY-MM-DD) */
  perDay: Record<string, number>
  /** Fetched from /api/costs — prompt cache stats */
  cacheStats: CacheStats
  /** Fetched from /api/costs — token totals for the period */
  tokenStats: { input: number; output: number; total: number }
  /** Fetched from /api/costs — token breakdown per day (key = YYYY-MM-DD) */
  tokensPerDay: Record<string, { input: number; output: number; cache_read: number }>
}

export interface AnalyticsSummary {
  days: number
  total: number
  completed: number
  failed: number
  running: number
  by_status: Record<string, number>
  per_day: Record<string, Record<string, number>>
  per_agent: Record<string, { total: number; completed: number; failed: number; cost: number }>
  production_queue: Record<string, number>
}

const TOOL_FEED_MAX = 200

const AGENTS_INIT: Record<string, AgentState> = {
  // --- Etsy Store ---
  research:          { status: 'idle', lastTask: '' },
  design:            { status: 'idle', lastTask: '' },
  publisher:         { status: 'idle', lastTask: '' },
  analytics:         { status: 'idle', lastTask: '' },
  finance:           { status: 'idle', lastTask: '' },
  // --- Personal ---
  recall:            { status: 'idle', lastTask: '' },
  watcher:           { status: 'idle', lastTask: '' },
  remind:            { status: 'idle', lastTask: '' },
  summarize:         { status: 'idle', lastTask: '' },
  research_personal: { status: 'idle', lastTask: '' },
}

interface AgentPeXIStore {
  /* WebSocket */
  wsConnected: boolean
  setWsConnected: (v: boolean) => void

  /* Agents */
  agents: Record<string, AgentState>
  setAgentStatus: (name: string, status: AgentState['status'], lastTask?: string) => void

  /* Tool Feed */
  toolEvents: ToolEvent[]
  addToolEvent: (evt: ToolEvent) => void

  /* System */
  systemStatus: SystemState
  setSystemStatus: (s: Partial<SystemState>) => void

  /* Agent Steps */
  agentSteps: Record<string, AgentStep[]>
  addAgentStep: (step: AgentStep) => void
  clearAgentSteps: (agent: string) => void

  /* Overlay */
  overlaySystem: string | null
  setOverlaySystem: (name: string | null) => void

  /* Selected Agent */
  selectedAgent: string | null
  setSelectedAgent: (name: string | null) => void

  /* LLM Stats */
  llmStats: LlmStats
  addLlmCall: (input: number, output: number, cost: number) => void
  setCostsData: (data: { total: number; perAgent: Record<string, number>; perDay: Record<string, number>; budgetMonthlyUsd?: number; runCost?: number; cacheStats?: CacheStats; tokenStats?: { input: number; output: number; total: number }; tokensPerDay?: Record<string, { input: number; output: number; cache_read: number }> }) => void

  /* Context state (from WS context_update) */
  contextState: ContextUpdateEvent | null
  setContextState: (ctx: ContextUpdateEvent) => void

  /* Analytics summary (from /api/analytics/summary) */
  analyticsSummary: AnalyticsSummary | null
  setAnalyticsSummary: (s: AnalyticsSummary) => void

  /* ChromaDB stats (from /api/memory/stats) */
  chromaStats: { available: boolean; count: number } | null
  setChromaStats: (s: { available: boolean; count: number }) => void

  /* WS connected timestamp */
  connectedAt: number | null
  setConnectedAt: (ts: number | null) => void

  /* Budget threshold */
  budgetMonthlyUsd: number | null

  /* Selected Task for detail overlay */
  selectedTaskId: string | null
  setSelectedTaskId: (id: string | null) => void

  /* Active zone — Shell navigation */
  activeZone: 'neural' | 'etsy' | 'personal' | 'system' | 'analytics'
  setActiveZone: (z: 'neural' | 'etsy' | 'personal' | 'system' | 'analytics') => void

  /* Active domain */
  activeDomain: 'etsy' | 'personal'
  setActiveDomain: (domain: 'etsy' | 'personal') => void

  /* Domain config — fetched from /api/domains/config */
  domainConfig: {
    etsy:     { name: string; agents: string[] }
    personal: { name: string; agents: string[] }
  } | null
  setDomainConfig: (cfg: { etsy: { name: string; agents: string[] }; personal: { name: string; agents: string[] } }) => void

  /* Autopilot (Header pill + SystemView) */
  autopilotStatus: 'running' | 'paused' | 'stopped'
  autopilotCurrentNiche: string | null
  autopilotItemsToday: number
  setAutopilotStatus: (s: 'running' | 'paused' | 'stopped', niche?: string | null) => void
  setAutopilotItemsToday: (n: number) => void

  /* Budget extended (Header mini bars) */
  imageCostToday: number
  feeCostToday: number
  setImageCostToday: (n: number) => void
  setFeeCostToday:   (n: number) => void

  /* Memory query feed — ultimi 20 eventi memory_query (HUD MemoryStreams) */
  memoryQueryFeed: Array<{ agent: string; collection: string; ids: string[]; ts: number }>
  pushMemoryQuery: (q: { agent: string; collection: string; ids: string[]; ts: number }) => void

  /* Knowledge bridge feed — ultimi 20 eventi cross-domain (HUD BridgeActivity) */
  bridgeFeed: Array<{ topic: string; source_etsy: string; source_personal: string; ts: number }>
  pushBridgeEvent: (e: { topic: string; source_etsy: string; source_personal: string; ts: number }) => void

  /* Brief overlay (ContextOverlay) */
  briefOpen: boolean
  setBriefOpen: (v: boolean) => void
}

export const useStore = create<AgentPeXIStore>((set) => ({
  wsConnected: false,
  setWsConnected: (v) => set({ wsConnected: v }),

  agents: { ...AGENTS_INIT },
  setAgentStatus: (name, status, lastTask) =>
    set((s) => ({
      agents: {
        ...s.agents,
        [name]: {
          status,
          lastTask: lastTask ?? s.agents[name]?.lastTask ?? '',
        },
      },
    })),

  toolEvents: [],
  addToolEvent: (evt) =>
    set((s) => {
      const next = [...s.toolEvents, evt]
      if (next.length > TOOL_FEED_MAX) next.splice(0, next.length - TOOL_FEED_MAX)
      return { toolEvents: next }
    }),

  systemStatus: { queueSize: 0, activeTasks: 0, uptime: '—', dailyCost: 0 },
  setSystemStatus: (partial) =>
    set((s) => ({ systemStatus: { ...s.systemStatus, ...partial } })),

  agentSteps: {},
  addAgentStep: (step) => set((state) => {
    const current = state.agentSteps[step.agent] ?? []
    // Dedup: ignora step con stesso id già presente (hydration + WS)
    if (current.some((s) => s.id === step.id)) return state
    const updated = [...current, step].slice(-50)
    return { agentSteps: { ...state.agentSteps, [step.agent]: updated } }
  }),
  clearAgentSteps: (agent) => set((state) => ({
    agentSteps: { ...state.agentSteps, [agent]: [] }
  })),

  overlaySystem: null,
  setOverlaySystem: (name) => set({ overlaySystem: name, selectedAgent: null }),

  selectedAgent: null,
  setSelectedAgent: (name) => set({ selectedAgent: name }),

  llmStats: {
    inputTokens: 0, outputTokens: 0, runCost: 0, totalCost: 0,
    perAgent: {}, perDay: {},
    cacheStats: { readTokens: 0, writeTokens: 0, savingsUsd: 0, efficiencyPct: 0 },
    tokenStats: { input: 0, output: 0, total: 0 },
    tokensPerDay: {},
  },
  addLlmCall: (input, output, cost) =>
    set((s) => ({
      llmStats: {
        ...s.llmStats,
        inputTokens: s.llmStats.inputTokens + input,
        outputTokens: s.llmStats.outputTokens + output,
        runCost: s.llmStats.runCost + cost,
      },
    })),
  setCostsData: ({ total, perAgent, perDay, budgetMonthlyUsd, runCost, cacheStats, tokenStats, tokensPerDay }) =>
    set((s) => ({
      llmStats: {
        ...s.llmStats,
        totalCost: total,
        perAgent,
        perDay,
        runCost: runCost !== undefined ? Math.max(runCost, s.llmStats.runCost) : s.llmStats.runCost,
        cacheStats: cacheStats ?? s.llmStats.cacheStats,
        tokenStats: tokenStats ?? s.llmStats.tokenStats,
        tokensPerDay: tokensPerDay ?? s.llmStats.tokensPerDay,
      },
      budgetMonthlyUsd: budgetMonthlyUsd ?? s.budgetMonthlyUsd,
    })),

  contextState: null,
  setContextState: (ctx) => set({ contextState: ctx }),

  analyticsSummary: null,
  setAnalyticsSummary: (s) => set({ analyticsSummary: s }),

  chromaStats: null,
  setChromaStats: (s) => set({ chromaStats: s }),

  connectedAt: null,
  setConnectedAt: (ts) => set({ connectedAt: ts }),

  budgetMonthlyUsd: null,

  selectedTaskId: null,
  setSelectedTaskId: (id) => set({ selectedTaskId: id }),

  activeZone: 'neural',
  setActiveZone: (z) => set({ activeZone: z }),

  activeDomain: 'personal',
  setActiveDomain: (domain) => set({ activeDomain: domain }),

  domainConfig: null,
  setDomainConfig: (cfg) => set({ domainConfig: cfg }),

  autopilotStatus: 'stopped',
  autopilotCurrentNiche: null,
  autopilotItemsToday: 0,
  setAutopilotStatus:     (s, niche) => set({ autopilotStatus: s, autopilotCurrentNiche: niche ?? null }),
  setAutopilotItemsToday: (n) => set({ autopilotItemsToday: n }),

  imageCostToday: 0,
  feeCostToday:   0,
  setImageCostToday: (n) => set({ imageCostToday: n }),
  setFeeCostToday:   (n) => set({ feeCostToday: n }),

  memoryQueryFeed: [],
  pushMemoryQuery: (q) =>
    set((s) => ({
      memoryQueryFeed: [...s.memoryQueryFeed, q].slice(-20),
    })),

  bridgeFeed: [],
  pushBridgeEvent: (e) =>
    set((s) => ({
      bridgeFeed: [...s.bridgeFeed, e].slice(-20),
    })),

  briefOpen: false,
  setBriefOpen: (v) => set({ briefOpen: v }),
}))
