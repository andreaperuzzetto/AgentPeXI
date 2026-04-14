import { useState, useRef, type KeyboardEvent } from 'react'
import { useStore } from '../../store'

interface Props {
  onSend: (content: string) => void
}

export function ChatInput({ onSend }: Props) {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const wsConnected = useStore((s) => s.wsConnected)

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || !wsConnected) return
    onSend(trimmed)
    /* add user message to store directly */
    useStore.getState().addMessage({
      id: crypto.randomUUID(),
      role: 'user',
      content: trimmed,
      timestamp: new Date().toISOString(),
    })
    setValue('')
    inputRef.current?.focus()
  }

  const handleKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div
      style={{
        borderTop: '1px solid var(--border-subtle)',
        padding: '8px 10px',
        display: 'flex',
        gap: 8,
        background: 'var(--bg-surface-1)',
      }}
    >
      <textarea
        ref={inputRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKey}
        placeholder={wsConnected ? 'Scrivi a Pepe...' : 'WebSocket disconnesso'}
        disabled={!wsConnected}
        rows={1}
        style={{
          flex: 1,
          resize: 'none',
          background: 'var(--bg-surface-2)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 2,
          padding: '6px 10px',
          color: 'var(--text-primary)',
          fontFamily: 'var(--font-body)',
          fontSize: '0.8125rem',
          lineHeight: 1.5,
          outline: 'none',
        }}
      />
      <button
        onClick={submit}
        disabled={!wsConnected || !value.trim()}
        aria-label="Invia messaggio"
        style={{
          padding: '6px 14px',
          background: wsConnected && value.trim() ? 'var(--accent)' : 'var(--accent-dim)',
          border: 'none',
          borderRadius: 2,
          color: 'var(--bg-base)',
          fontFamily: 'var(--font-display)',
          fontWeight: 700,
          fontSize: '0.6875rem',
          letterSpacing: '0.06em',
          textTransform: 'uppercase' as const,
          cursor: wsConnected && value.trim() ? 'pointer' : 'default',
          transition: 'background 100ms var(--ease-out-quart)',
        }}
        onMouseEnter={(e) => {
          if (wsConnected && value.trim())
            (e.target as HTMLButtonElement).style.background = 'var(--accent-hover)'
        }}
        onMouseLeave={(e) => {
          (e.target as HTMLButtonElement).style.background =
            wsConnected && value.trim() ? 'var(--accent)' : 'var(--accent-dim)'
        }}
      >
        Invia
      </button>
    </div>
  )
}
