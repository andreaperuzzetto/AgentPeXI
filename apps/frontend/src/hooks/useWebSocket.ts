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
    case 'agent_started':
      store.setAgentStatus(data.agent, 'running', data.description ?? `task ${data.task_id.slice(0, 8)}`)
      break

    case 'agent_completed':
      store.setAgentStatus(data.agent, 'idle')
      break

    case 'agent_error':
      store.setAgentStatus(data.agent, 'error', data.error)
      break

    case 'system_status':
      store.setSystemStatus({
        queueSize:   data.queue_size,
        activeTasks: data.active_tasks,
        mock_mode:   data.mock_mode,
      })
      // Autopilot status — backend può includere autopilot_status nel system_status event
      if (data.autopilot_status) {
        store.setAutopilotStatus(data.autopilot_status, data.autopilot_niche ?? null)
      }
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
      store.setContextState(data)
      break

    case 'watcher_status':
      store.setAgentStatus(
        'watcher',
        data.status === 'active' ? 'running' : data.status === 'error' ? 'error' : 'idle',
        data.last_task ?? (data.last_capture_app ? `Ultima: ${data.last_capture_app}` : undefined),
      )
      break

    case 'watcher_capture':
      store.addAgentStep({
        id: data.step_id ?? crypto.randomUUID(),
        agent: 'watcher',
        taskId: data.task_id ?? 'watcher',
        stepNumber: data.step_number ?? 0,
        stepType: data.step_type ?? 'capture',
        description: data.description ?? `${data.app_name} — ${data.chunks} chunk`,
        durationMs: data.duration_ms ?? 0,
        timestamp: data.timestamp,
      })
      break

    case 'domain_switched':
      if (data.domain === 'etsy' || data.domain === 'personal') {
        store.setActiveDomain(data.domain)
      }
      break

    case 'memory_query': {
      const mq = {
        agent:      data.agent,
        collection: data.collection,
        ids:        data.ids,
        ts:         Date.now(),
      }
      store.pushMemoryQuery(mq)       // feed HUD MemoryStreams (FE-3)
      break
    }

    case 'knowledge_bridge':
      store.pushBridgeEvent({
        topic:           data.topic   ?? '',
        source_etsy:     data.source_etsy     ?? '',
        source_personal: data.source_personal ?? '',
        ts:              data.ts ?? Date.now(),
      })
      break

    default:
      break
  }
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectDelay = useRef(RECONNECT_BASE)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  useEffect(() => {
    let unmounted = false

    async function hydrateOnConnect() {
      const store = useStore.getState()

      // Ripristina stati sistema (mock_mode, agenti ecc.)
      try {
        const r = await fetch('/api/status')
        if (r.ok) {
          const data = await r.json()
          store.setSystemStatus({ mock_mode: data.mock_mode ?? false })
        }
      } catch {}

      // Ripristina stato ScreenWatcher
      try {
        const r = await fetch('/api/screen/status')
        if (r.ok) {
          const sw = await r.json()
          if (sw.available) {
            store.setAgentStatus(
              'watcher',
              sw.active ? 'running' : 'idle',
              sw.last_capture_app ? `Ultima: ${sw.last_capture_app}` : '',
            )
          }
        }
      } catch {}

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
        hydrateOnConnect()
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

    connect()

    return () => {
      unmounted = true
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
      wsRef.current = null
      useStore.getState().setWsConnected(false)
    }
  }, [])
}
