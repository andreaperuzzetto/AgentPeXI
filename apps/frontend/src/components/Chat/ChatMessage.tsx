import type { ChatMessage as ChatMessageType } from '../../types'

const ROLE_STYLES: Record<string, { bg: string; label: string; labelColor: string }> = {
  user:   { bg: 'var(--bg-surface-2)', label: 'Tu',     labelColor: 'var(--text-muted)' },
  pepe:   { bg: 'var(--bg-surface-1)', label: 'Pepe',   labelColor: 'var(--accent)' },
  system: { bg: 'transparent',         label: 'Sistema', labelColor: 'var(--text-faint)' },
}

export function ChatMessage({ msg }: { msg: ChatMessageType }) {
  const style = ROLE_STYLES[msg.role] ?? ROLE_STYLES.system

  if (msg.role === 'system') {
    return (
      <div style={{ padding: '4px 12px', fontSize: '0.75rem', color: 'var(--text-faint)' }}>
        {msg.content}
      </div>
    )
  }

  return (
    <div
      style={{
        padding: '8px 12px',
        background: style.bg,
        borderRadius: 2,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 2 }}>
        <span
          style={{
            fontFamily: 'var(--font-display)',
            fontWeight: 700,
            fontSize: '0.6875rem',
            letterSpacing: '0.04em',
            textTransform: 'uppercase' as const,
            color: style.labelColor,
          }}
        >
          {style.label}
        </span>
        <span className="font-data" style={{ fontSize: '0.625rem', color: 'var(--text-faint)' }}>
          {formatTime(msg.timestamp)}
        </span>
      </div>
      <div style={{ color: 'var(--text-primary)', lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>
        {msg.content}
      </div>
    </div>
  )
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ''
  }
}
