/**
 * useMemoryGraph — fetch + poll + WS activation for NeuralBrainOrb
 *
 * - Fetches GET /api/memory/graph at mount
 * - Falls back to mockGraphData if nodes.length === 0 (DEV / no-API)
 * - Silently refetches every 60s (diffed update — no visual flash)
 * - Listens to store.memoryQueryFeed changes → calls addActiveNodeId for each id
 */

import { useEffect, useRef } from 'react'
import { useStore }          from '../store'
import { MOCK_NODES, MOCK_EDGES } from '../components/NeuralBrainOrb/mockGraphData'
import type { GraphNode, GraphEdge } from '../components/NeuralBrainOrb/NodeDrawer'

interface GraphAPIResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

async function fetchGraph(): Promise<GraphAPIResponse> {
  const r = await fetch('/api/memory/graph?threshold=0.68')
  if (!r.ok) throw new Error(`graph fetch ${r.status}`)
  return r.json() as Promise<GraphAPIResponse>
}

function applyGraph(data: GraphAPIResponse) {
  const store = useStore.getState()
  const nodes = data.nodes?.length ? data.nodes : MOCK_NODES
  const edges = data.edges?.length ? data.edges : MOCK_EDGES
  store.setMemoryGraph({ nodes, edges })
}

export function useMemoryGraph() {
  const memoryQueryFeed    = useStore(s => s.memoryQueryFeed)
  const addActiveNodeId    = useStore(s => s.addActiveNodeId)
  const lastPulseTsRef     = useRef(0)
  const intervalRef        = useRef<ReturnType<typeof setInterval>>(undefined)

  /* Initial fetch + 60s polling */
  useEffect(() => {
    async function load() {
      try {
        const data = await fetchGraph()
        applyGraph(data)
      } catch {
        // API unavailable — use mock data
        useStore.getState().setMemoryGraph({ nodes: MOCK_NODES, edges: MOCK_EDGES })
      }
    }

    load()
    intervalRef.current = setInterval(load, 60_000)

    return () => clearInterval(intervalRef.current)
  }, [])

  /* WS activation — memory_query events pulse matching nodes */
  useEffect(() => {
    if (memoryQueryFeed.length === 0) return
    const latest = memoryQueryFeed[memoryQueryFeed.length - 1]
    if (latest.ts <= lastPulseTsRef.current) return
    lastPulseTsRef.current = latest.ts
    latest.ids.forEach(id => addActiveNodeId(id))
  }, [memoryQueryFeed, addActiveNodeId])
}
