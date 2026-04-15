import { useState, useRef, type KeyboardEvent } from 'react'
import { useStore } from '../../store'

export function ChatInput() {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const wsConnected = useStore((s) => s.wsConnected)
  const wsSend = useStore((s) => s.wsSend)

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || !wsConnected || !wsSend) return
    wsSend(trimmed)
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
    if (e.key === 'Enter') {
      e.preventDefault()
      submit()
    }
  }

  const canSend = wsConnected && value.trim().length > 0

  return (
    /* Matches prototype .chat-in */
    <div
      style={{
        padding: '8px 10px',
        display: 'flex',
        gap: 7,
        alignItems: 'center',
        minWidth: 0,
      }}
    >
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKey}
        placeholder={wsConnected ? 'Scrivi a Pepe…' : 'WebSocket disconnesso'}
        disabled={!wsConnected}
        style={{
          flex: 1,
          background: 'var(--s2)',
          border: '1px solid var(--b0)',
          borderRadius: 7,
          padding: '7px 11px',
          color: 'var(--tp)',
          fontFamily: 'var(--fb)',
          fontSize: 14,
          outline: 'none',
          transition: `border-color .25s var(--e-io), background .25s var(--e-io)`,
        }}
        onFocus={(e) => {
          e.target.style.borderColor = 'var(--b1)'
          e.target.style.background = 'var(--s3)'
        }}
        onBlur={(e) => {
          e.target.style.borderColor = 'var(--b0)'
          e.target.style.background = 'var(--s2)'
        }}
      />

      {/* Icon send button — matches prototype .chat-in button */}
      <button
        onClick={submit}
        disabled={!canSend}
        aria-label="Invia messaggio"
        style={{
          background: canSend ? 'var(--accent)' : 'rgba(45,232,106,.2)',
          border: 'none',
          borderRadius: 7,
          width: 34,
          height: 34,
          flexShrink: 0,
          cursor: canSend ? 'pointer' : 'default',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transition: `opacity .2s var(--e-io), transform .18s var(--e-spring)`,
        }}
        onMouseEnter={(e) => {
          if (canSend) (e.currentTarget as HTMLButtonElement).style.opacity = '0.85'
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLButtonElement).style.opacity = '1'
        }}
        onMouseDown={(e) => {
          if (canSend) (e.currentTarget as HTMLButtonElement).style.transform = 'scale(.94)'
        }}
        onMouseUp={(e) => {
          (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1)'
        }}
      >
        {/* Send arrow SVG */}
        <svg
          viewBox="0 0 16 16"
          xmlns="http://www.w3.org/2000/svg"
          style={{ width: 15, height: 15, fill: '#0b1410' }}
        >
          <path d="M15 8L2 2l5 6-5 6L15 8z" />
        </svg>
      </button>
    </div>
  )
}
