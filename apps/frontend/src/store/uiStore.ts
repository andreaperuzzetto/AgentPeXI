import { create } from 'zustand'

export type OrbState = 'wakeword' | 'listening' | 'thinking' | 'speaking'

export interface VoiceNotification {
  id: string
  type: 'error' | 'warning'
  message: string   // frase corta già detta da Pepe
  detail: string    // testo tecnico completo → green card
  agent: string
  ts: string        // ISO timestamp
}

interface UiStore {
  orbState: OrbState
  reasoningText: string
  isSpeaking: boolean
  notifications: VoiceNotification[]
  setOrbState: (state: OrbState) => void
  setReasoningText: (text: string) => void
  setIsSpeaking: (speaking: boolean) => void
  pushNotification: (n: Omit<VoiceNotification, 'id'>) => void
  dismissNotification: (id: string) => void
}

export const useUiStore = create<UiStore>((set) => ({
  orbState: 'wakeword',
  reasoningText: '',
  isSpeaking: false,
  notifications: [],
  setOrbState: (state) => set({ orbState: state }),
  setReasoningText: (text) => set({ reasoningText: text }),
  setIsSpeaking: (speaking) => set({ isSpeaking: speaking }),
  pushNotification: (n) =>
    set((s) => ({
      notifications: [
        ...s.notifications,
        { ...n, id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}` },
      ],
    })),
  dismissNotification: (id) =>
    set((s) => ({ notifications: s.notifications.filter((n) => n.id !== id) })),
}))
