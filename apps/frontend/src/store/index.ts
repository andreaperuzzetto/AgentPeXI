import { create } from 'zustand'
import type { ChatMessage, AgentState, ToolEvent, SystemState, Session, AgentStep } from '../types'

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
}

const TOOL_FEED_MAX = 200

const AGENTS_INIT: Record<string, AgentState> = {
  research:         { status: 'idle', lastTask: '' },
  design:           { status: 'idle', lastTask: '' },
  publisher:        { status: 'idle', lastTask: '' },
  analytics:        { status: 'idle', lastTask: '' },
  customer_service: { status: 'idle', lastTask: '' },
  finance:          { status: 'idle', lastTask: '' },
}

interface AgentPeXIStore {
  /* WebSocket */
  wsConnected: boolean
  setWsConnected: (v: boolean) => void

  /* Sessions */
  sessionId: string | null
  sessions: Session[]
  setSessionId: (id: string | null) => void
  setSessions: (s: Session[]) => void

  /* Chat */
  messages: ChatMessage[]
  isTyping: boolean
  addMessage: (msg: ChatMessage) => void
  setIsTyping: (v: boolean) => void

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
  setCostsData: (data: { total: number; perAgent: Record<string, number>; perDay: Record<string, number> }) => void

  /* WS Send */
  wsSend: ((content: string) => void) | null
  setWsSend: (fn: ((content: string) => void) | null) => void
}

export const useStore = create<AgentPeXIStore>((set) => ({
  wsConnected: false,
  setWsConnected: (v) => set({ wsConnected: v }),

  sessionId: null,
  sessions: [],
  setSessionId: (id) => set({ sessionId: id, messages: [] }),
  setSessions: (s) => set({ sessions: s }),

  messages: [],
  isTyping: false,
  addMessage: (msg) =>
    set((s) => ({ messages: [...s.messages, msg], isTyping: false })),
  setIsTyping: (v) => set({ isTyping: v }),

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

  llmStats: { inputTokens: 0, outputTokens: 0, runCost: 0, totalCost: 0, perAgent: {}, perDay: {} },
  addLlmCall: (input, output, cost) =>
    set((s) => ({
      llmStats: {
        ...s.llmStats,
        inputTokens: s.llmStats.inputTokens + input,
        outputTokens: s.llmStats.outputTokens + output,
        runCost: s.llmStats.runCost + cost,
      },
    })),
  setCostsData: ({ total, perAgent, perDay }) =>
    set((s) => ({
      llmStats: { ...s.llmStats, totalCost: total, perAgent, perDay },
    })),

  wsSend: null,
  setWsSend: (fn) => set({ wsSend: fn }),
}))
