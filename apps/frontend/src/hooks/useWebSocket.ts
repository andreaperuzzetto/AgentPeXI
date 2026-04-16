import { useEffect, useRef } from 'react'
import { useStore } from '../store'
import type { WSIncoming } from '../types'

const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

const RECONNECT_BASE = 1000
const RECONNECT_MAX = 16000

function handleMessage(raw: MessageEvent) {
  let data: WSIncoming
  try {
    data = JSON.parse(raw.data as string)
  } catch {
    return
  }

  const store = useStore.getState()

  switch (data.type) {
    case 'pepe_message':
      store.addMessage({
        id: crypto.randomUUID(),
        role: 'pepe',
        content: data.content,
        timestamp: data.timestamp,
      })
      break

    case 'agent_started': {
      const desc = data.description ?? `task ${data.task_id.slice(0, 8)}`
      store.setAgentStatus(data.agent, 'running', desc)
      store.addMessage({
        id: crypto.randomUUID(),
        role: 'system',
        content: `Agente ${data.agent} avviato: ${desc}`,
        timestamp: new Date().toISOString(),
      })
      break
    }

    case 'agent_completed':
      store.setAgentStatus(data.agent, 'idle')
      break

    case 'agent_error':
      store.setAgentStatus(data.agent, 'error', data.error)
      store.addMessage({
        id: crypto.randomUUID(),
        role: 'system',
        content: `Errore agente ${data.agent}: ${data.error}`,
        timestamp: new Date().toISOString(),
      })
      break

    case 'system_status':
      store.setSystemStatus({
        queueSize: data.queue_size,
        activeTasks: data.active_tasks,
      })
      break

    case 'tool_call':
      store.addToolEvent({
        id: crypto.randomUUID(),
        agent: data.agent,
        tool: data.tool,
        action: data.action,
        status: data.status,
        duration_ms: data.duration_ms,
        cost_usd: data.cost_usd,
        timestamp: data.timestamp,
      })
      break

    case 'agent_step':
      store.addAgentStep({
        id: data.step_id,
        agent: data.agent,
        taskId: data.task_id,
        stepNumber: data.step_number,
        stepType: data.step_type,
        description: data.description,
        durationMs: data.duration_ms,
        timestamp: data.timestamp,
      })
      break

    case 'llm_call':
      store.addLlmCall(data.input_tokens, data.output_tokens, data.cost_usd)
      break

    case 'context_update':
      store.setContextState(data as any)
      break

    default:
      break
  }
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectDelay = useRef(RECONNECT_BASE)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const connectedAt = useRef<Date | null>(null)

  useEffect(() => {
    let unmounted = false

    function send(content: string) {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) return
      const sessionId = useStore.getState().sessionId || 'default'
      ws.send(JSON.stringify({ type: 'user_message', content, session_id: sessionId }))
      useStore.getState().setIsTyping(true)
    }

    function scheduleReconnect() {
      if (unmounted) return
      clearTimeout(reconnectTimer.current)
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(reconnectDelay.current * 2, RECONNECT_MAX)
        connect()
      }, reconnectDelay.current)
    }

    function connect() {
      if (unmounted) return
      if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return

      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.addEventListener('open', () => {
        useStore.getState().setWsConnected(true)
        useStore.getState().setConnectedAt(Date.now())
        reconnectDelay.current = RECONNECT_BASE
        connectedAt.current = new Date()
      })

      ws.addEventListener('message', handleMessage)

      ws.addEventListener('close', () => {
        useStore.getState().setWsConnected(false)
        scheduleReconnect()
      })

      ws.addEventListener('error', () => {
        ws.close()
      })
    }

    useStore.getState().setWsSend(send)
    connect()

    return () => {
      unmounted = true
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
      wsRef.current = null
      useStore.getState().setWsConnected(false)
      useStore.getState().setWsSend(null)
    }
  }, [])

  return {
    getUptime() {
      if (!connectedAt.current) return '—'
      const diff = Date.now() - connectedAt.current.getTime()
      const minutes = Math.floor(diff / 60000)
      const hours = Math.floor(minutes / 60)
      const mins = minutes % 60
      if (hours > 0) return `${hours}h ${mins}m`
      return `${mins}m`
    },
  }
}
