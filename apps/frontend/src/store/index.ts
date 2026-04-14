import { create } from 'zustand'
import type { ChatMessage, AgentState, ToolEvent, SystemState } from '../types'

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
}

export const useStore = create<AgentPeXIStore>((set) => ({
  wsConnected: false,
  setWsConnected: (v) => set({ wsConnected: v }),

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
}))
