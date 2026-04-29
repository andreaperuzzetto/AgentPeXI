/**
 * forceWorker.ts — D3-force-3d simulation in a dedicated Web Worker
 *
 * Protocol (main → worker):
 *   { type: 'init',          nodes, edges, params: { alphaTarget, charge } }
 *   { type: 'update_params', params: { alphaTarget, charge } }
 *   { type: 'stop' }
 *
 * Protocol (worker → main):
 *   { positions: Float32Array }   ← transferable, x/y/z per node packed flat
 *
 * Accessed via Vite Worker import:
 *   new Worker(new URL('../workers/forceWorker.ts', import.meta.url), { type: 'module' })
 */

import {
  forceSimulation,
  forceLink,
  forceManyBody,
  forceCenter,
  forceCollide,
  type SimulationNodeDatum,
  type SimulationLinkDatum,
} from 'd3-force-3d'

/* ── Types ──────────────────────────────────────────────────────────────── */

interface WorkerNode extends SimulationNodeDatum {
  id: string
  zone?: string
}

interface WorkerEdge extends SimulationLinkDatum<WorkerNode> {
  source: string
  target: string
}

interface PhysicsParams {
  alphaTarget: number
  charge: number
}

type IncomingMessage =
  | { type: 'init';          nodes: WorkerNode[]; edges: WorkerEdge[]; params: PhysicsParams }
  | { type: 'update_params'; params: PhysicsParams }
  | { type: 'stop' }

/* ── State ──────────────────────────────────────────────────────────────── */

let simulation: ReturnType<typeof forceSimulation<WorkerNode>> | null = null
let simNodes: WorkerNode[] = []

function rand(): number {
  return (Math.random() - 0.5) * 30
}

function sendPositions(): void {
  const buf = new Float32Array(simNodes.length * 3)
  for (let i = 0; i < simNodes.length; i++) {
    buf[i * 3]     = simNodes[i].x ?? 0
    buf[i * 3 + 1] = simNodes[i].y ?? 0
    buf[i * 3 + 2] = simNodes[i].z ?? 0
  }
  // Transfer ownership of the ArrayBuffer — zero-copy to main thread.
  // Options-object form matches WindowPostMessageOptions.transfer and avoids
  // the overload ambiguity between Window (targetOrigin: string) and Worker global.
  self.postMessage({ positions: buf }, { transfer: [buf.buffer] })
}

/* ── Message handler ────────────────────────────────────────────────────── */

self.onmessage = ({ data }: MessageEvent<IncomingMessage>) => {
  if (data.type === 'init') {
    // Scatter nodes in 3D space initially so the simulation has room to converge
    simNodes = data.nodes.map(n => ({ ...n, x: rand(), y: rand(), z: rand() }))
    const simEdges: SimulationLinkDatum<WorkerNode>[] = data.edges.map(e => ({
      source: e.source,
      target: e.target,
    }))

    simulation = forceSimulation<WorkerNode>(simNodes, 3)   // 3 = spatial dimensions
      .force(
        'link',
        forceLink<WorkerNode, SimulationLinkDatum<WorkerNode>>(simEdges)
          .id((d) => d.id)
          .distance(8)
          .strength(0.3),
      )
      .force('charge', forceManyBody<WorkerNode>().strength(data.params.charge ?? -40))
      .force('center', forceCenter<WorkerNode>(0, 0, 0).strength(0.05))
      .force('collide', forceCollide<WorkerNode>(2))
      .alphaTarget(data.params.alphaTarget ?? 0.05)
      .alphaDecay(0.002)   // very slow decay → perpetual gentle movement
      .on('tick', sendPositions)

    return
  }

  if (data.type === 'update_params') {
    if (!simulation) return
    const chargeForce = simulation.force('charge') as ReturnType<typeof forceManyBody> | undefined
    chargeForce?.strength(data.params.charge)
    simulation.alphaTarget(data.params.alphaTarget)
    // No restart / no alpha kick — the main thread sends incremental lerped values
    // every few frames (FE-2.3), so alpha tracks the gradually changing target naturally.
    // The simulation runs continuously via alphaDecay(0.002) + perpetual tick.
    return
  }

  if (data.type === 'stop') {
    simulation?.stop()
    simulation = null
  }
}
