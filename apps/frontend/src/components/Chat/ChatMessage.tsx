import { useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatMessage as ChatMessageType } from '../../types'
import { useTypewriter } from '../../hooks/useTypewriter'
import { useStore } from '../../store'

export function ChatMessage({ msg }: { msg: ChatMessageType }) {
  if (msg.role === 'system') {
    return (
      <div
        style={{
          padding: '3px 0',
          fontFamily: 'var(--fd)',
          fontSize: 12,
          color: 'var(--tf)',
          letterSpacing: '0.03em',
        }}
      >
        {msg.content}
      </div>
    )
  }

  const isUser = msg.role === 'user'
  const isPepe = msg.role === 'pepe'
  const typing = isPepe && !!msg.isNew
  const displayed = useTypewriter(msg.content, typing)
  const markMessageShown = useStore((s) => s.markMessageShown)

  useEffect(() => {
    if (typing && displayed === msg.content) {
      markMessageShown(msg.id)
    }
  }, [typing, displayed, msg.content, msg.id, markMessageShown])

  return (
    <div
      className="animate-msg-in"
      style={{ display: 'flex', flexDirection: 'column', gap: 2 }}
    >
      {/* Role label — .mr style */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 11,
          letterSpacing: '0.06em',
          textTransform: 'uppercase' as const,
          color: isUser ? 'var(--tm)' : 'var(--accent)',
        }}
      >
        {isUser ? 'Tu' : 'Pepe'}
      </span>

      {/* Bubble — asymmetric border-radius like prototype */}
      <div
        className={isUser ? undefined : 'chat-md'}
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
          alignSelf: isUser ? 'flex-end' : 'flex-start',
          maxWidth: '92%',
          ...(isUser ? { whiteSpace: 'pre-wrap' as const } : {}),
        }}
      >
        {isUser ? msg.content : (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayed}</ReactMarkdown>
        )}
      </div>

      {/* Timestamp */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 11,
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
