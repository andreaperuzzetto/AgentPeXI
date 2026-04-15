import type { ChatMessage as ChatMessageType } from '../../types'

export function ChatMessage({ msg }: { msg: ChatMessageType }) {
  if (msg.role === 'system') {
    return (
      <div
        style={{
          padding: '3px 0',
          fontFamily: 'var(--fd)',
          fontSize: 10,
          color: 'var(--tf)',
          letterSpacing: '0.03em',
        }}
      >
        {msg.content}
      </div>
    )
  }

  const isUser = msg.role === 'user'

  return (
    <div
      className="animate-msg-in"
      style={{ display: 'flex', flexDirection: 'column', gap: 2 }}
    >
      {/* Role label — .mr style */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 9,
          letterSpacing: '0.06em',
          textTransform: 'uppercase' as const,
          color: isUser ? 'var(--tm)' : 'var(--accent)',
        }}
      >
        {isUser ? 'Tu' : 'Pepe'}
      </span>

      {/* Bubble — asymmetric border-radius like prototype */}
      <div
        style={{
          padding: '8px 11px',
          borderRadius: isUser ? '9px 3px 9px 9px' : '3px 9px 9px 9px',
          fontSize: 14,
          lineHeight: 1.55,
          background: isUser ? 'var(--adim)' : 'var(--s2)',
          border: isUser
            ? '1px solid rgba(45,232,106,.13)'
            : '1px solid var(--b0)',
          color: 'var(--tp)',
          whiteSpace: 'pre-wrap' as const,
          alignSelf: isUser ? 'flex-end' : 'flex-start',
          maxWidth: '92%',
        }}
      >
        {msg.content}
      </div>

      {/* Timestamp */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 9,
          color: 'var(--tf)',
          alignSelf: isUser ? 'flex-end' : 'flex-start',
          marginTop: 1,
        }}
      >
        {formatTime(msg.timestamp)}
      </span>
    </div>
  )
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('it-IT', {
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ''
  }
}
