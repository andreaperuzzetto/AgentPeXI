import { useEffect, useRef, useCallback } from 'react'
import { useStore } from '../store'
import type { WSIncoming } from '../types'

const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/chat`

const RECONNECT_BASE = 1000
const RECONNECT_MAX = 16000

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectDelay = useRef(RECONNECT_BASE)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const unmounted = useRef(false)

  const handleMessage = useCallback((raw: MessageEvent) => {
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
          timestamp: data.timestamp,
        })
        break

      /* agent_step, llm_call, subagent_spawn — ricevuti, ignorati in Fase 1 */
      default:
        break
    }
  }, [])

  const connect = useCallback(() => {
    if (unmounted.current) return
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.addEventListener('open', () => {
      useStore.getState().setWsConnected(true)
      reconnectDelay.current = RECONNECT_BASE
    })

    ws.addEventListener('message', handleMessage)

    ws.addEventListener('close', () => {
      useStore.getState().setWsConnected(false)
      scheduleReconnect()
    })

    ws.addEventListener('error', () => {
      ws.close()
    })
  }, [handleMessage])

  const scheduleReconnect = useCallback(() => {
    if (unmounted.current) return
    clearTimeout(reconnectTimer.current)
    reconnectTimer.current = setTimeout(() => {
      reconnectDelay.current = Math.min(reconnectDelay.current * 2, RECONNECT_MAX)
      connect()
    }, reconnectDelay.current)
  }, [connect])

  const send = useCallback((content: string) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'user_message', content }))
    useStore.getState().setIsTyping(true)
  }, [])

  useEffect(() => {
    unmounted.current = false
    connect()
    return () => {
      unmounted.current = true
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { send }
}
