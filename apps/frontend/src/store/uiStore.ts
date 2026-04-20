import { create } from 'zustand'

export type OrbState = 'wakeword' | 'listening' | 'thinking' | 'speaking'

interface UiStore {
  orbState: OrbState
  reasoningText: string
  isSpeaking: boolean
  setOrbState: (state: OrbState) => void
  setReasoningText: (text: string) => void
  setIsSpeaking: (speaking: boolean) => void
}

export const useUiStore = create<UiStore>((set) => ({
  orbState: 'wakeword',
  reasoningText: '',
  isSpeaking: false,
  setOrbState: (state) => set({ orbState: state }),
  setReasoningText: (text) => set({ reasoningText: text }),
  setIsSpeaking: (speaking) => set({ isSpeaking: speaking }),
}))
