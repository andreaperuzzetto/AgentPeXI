/**
 * NeuralBrainOrb — 3d-force-graph memory visualization
 *
 * Replaces the hand-rolled Three.js renderer with ForceGraph3D.
 * Handles: nodeColor by collection, nodeVal by connections, hover dimming,
 * activeNodeIds pulse (from WS memory_query), click → NodeDrawer.
 */

import { useEffect, useRef, useCallback }    from 'react'
import ForceGraph3D, { type ForceGraph3DInstance } from '3d-force-graph'
import * as THREE                             from 'three'
import { useStore }                           from '../../store'
import { useMemoryGraph }                     from '../../hooks/useMemoryGraph'
import type { GraphNode, GraphEdge }          from './NodeDrawer'

/* ── Collection palette ─────────────────────────────────────────────────── */

export const COLL_COLOR: Record<string, string> = {
  pepe_memory:     '#F5A623',
  personal_memory: '#1BFF5E',
  screen_memory:   '#B57BFF',
  shared_memory:   '#C8C8FF',
}

function collColor(collection: string): string {
  return COLL_COLOR[collection] ?? '#8B8D98'
}

function nodeRadius(connections: number): number {
  if (connections >= 10) return 9
  if (connections >= 4)  return 6
  if (connections >= 1)  return 4
  return 2.5
}

function hexToRgb(hex: string) {
  const v = parseInt(hex.replace('#', ''), 16)
  return { r: (v >> 16) & 0xff, g: (v >> 8) & 0xff, b: v & 0xff }
}

function lerpHex(a: string, b: string, t: number): string {
  const ca = hexToRgb(a), cb = hexToRgb(b)
  const r = Math.round(ca.r + (cb.r - ca.r) * t)
  const g = Math.round(ca.g + (cb.g - ca.g) * t)
  const bl = Math.round(ca.b + (cb.b - ca.b) * t)
  return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${bl.toString(16).padStart(2,'0')}`
}

/* ── Types ──────────────────────────────────────────────────────────────── */

interface GNode extends GraphNode {
  __threeObj?: THREE.Mesh
  // d3-force-3d adds these at runtime
  x?: number; y?: number; z?: number
}

interface GLink extends Omit<GraphEdge, 'source' | 'target'> {
  source: string | GNode
  target: string | GNode
}

interface Props {
  onNodeClick: (node: GraphNode) => void
  onBackgroundClick: () => void
  selectedNodeId: string | null
}

/* ── Component ──────────────────────────────────────────────────────────── */

export function NeuralBrainOrb({ onNodeClick, onBackgroundClick, selectedNodeId }: Props) {
  useMemoryGraph()

  const containerRef   = useRef<HTMLDivElement>(null)
  const graphRef       = useRef<ForceGraph3DInstance<GNode, GLink> | null>(null)
  const meshMapRef     = useRef<Map<string, THREE.Mesh>>(new Map())
  const hoveredIdRef   = useRef<string | null>(null)
  const edgesRef       = useRef<GraphEdge[]>([])
  const nodeMapRef     = useRef<Map<string, GraphNode>>(new Map())

  const memoryGraph  = useStore(s => s.memoryGraph)
  const activeNodeIds = useStore(s => s.activeNodeIds)

  /* ── Init ForceGraph3D ─────────────────────────────────────────────────── */

  useEffect(() => {
    if (!containerRef.current) return
    const el = containerRef.current

    const _raw = new ForceGraph3D(el, { rendererConfig: { alpha: true, antialias: true } })
    const graph = _raw as unknown as ForceGraph3DInstance<GNode, GLink>
    graph
      .backgroundColor('rgba(0,0,0,0)')
      .nodeVal((n: GNode) => {
        const r = nodeRadius(n.connections ?? 0)
        return r * r   // nodeVal is proportional to volume; scale²→visual radius
      })
      .nodeThreeObject((n: GNode) => {
        const r = nodeRadius(n.connections ?? 0)
        const col = collColor(n.collection)
        const geo = new THREE.SphereGeometry(r, 18, 14)
        const mat = new THREE.MeshStandardMaterial({
          color:             new THREE.Color(col),
          emissive:          new THREE.Color(col),
          emissiveIntensity: 0.25,
          transparent:       true,
          opacity:           0.9,
          roughness:         0.5,
          metalness:         0.1,
        })
        const mesh = new THREE.Mesh(geo, mat)
        meshMapRef.current.set(n.id, mesh)
        return mesh
      })
      .nodeThreeObjectExtend(false)
      .linkColor((l: GLink) => {
        const srcId = typeof l.source === 'string' ? l.source : l.source.id
        const tgtId = typeof l.target === 'string' ? l.target : l.target.id
        const srcCol = collColor(nodeMapRef.current.get(srcId)?.collection ?? '')
        const tgtCol = collColor(nodeMapRef.current.get(tgtId)?.collection ?? '')
        return lerpHex(srcCol, tgtCol, 0.5)
      })
      .linkWidth((l: GLink) => {
        const w = l.weight ?? 0.8
        return 0.3 + (w - 0.72) / (1.0 - 0.72) * (1.8 - 0.3)
      })
      .linkOpacity(0.18)
      .onNodeClick((n: GNode, event: MouseEvent) => {
        event.stopPropagation()
        onNodeClick(n)
      })
      .onBackgroundClick(() => onBackgroundClick())
      .onNodeHover((n: GNode | null) => {
        hoveredIdRef.current = n?.id ?? null
        applyHoverOpacity(n?.id ?? null)
      })

    // add ambient + directional lights
    const scene = graph.scene()
    scene.add(new THREE.AmbientLight(0xffffff, 0.6))
    const dir = new THREE.DirectionalLight(0xffffff, 0.8)
    dir.position.set(40, 80, 60)
    scene.add(dir)

    graphRef.current = graph
    return () => {
      graph.pauseAnimation()
      const renderer = graph.renderer()
      renderer.dispose()
      renderer.forceContextLoss()
      el.innerHTML = ''
      graphRef.current = null
      meshMapRef.current.clear()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ── Load graph data ───────────────────────────────────────────────────── */

  useEffect(() => {
    if (!graphRef.current) return
    if (!memoryGraph.nodes.length) return

    edgesRef.current = memoryGraph.edges
    meshMapRef.current.clear()

    const newNodeMap = new Map<string, GraphNode>()
    memoryGraph.nodes.forEach(n => newNodeMap.set(n.id, n))
    nodeMapRef.current = newNodeMap

    const graphData = {
      nodes: memoryGraph.nodes.map(n => ({ ...n })) as GNode[],
      links: memoryGraph.edges.map(e => ({ ...e })) as GLink[],
    }
    graphRef.current.graphData(graphData)
  }, [memoryGraph])

  /* ── Resize observer ───────────────────────────────────────────────────── */

  useEffect(() => {
    if (!containerRef.current || !graphRef.current) return
    const obs = new ResizeObserver(() => {
      if (!containerRef.current || !graphRef.current) return
      const { width, height } = containerRef.current.getBoundingClientRect()
      graphRef.current.width(width).height(height)
    })
    obs.observe(containerRef.current)
    return () => obs.disconnect()
  }, [])

  /* ── Apply opacity based on hover/active state ─────────────────────────── */

  const applyHoverOpacity = useCallback((hovId: string | null) => {
    if (!hovId) {
      meshMapRef.current.forEach(mesh => {
        ;(mesh.material as THREE.MeshStandardMaterial).opacity = 0.9
        ;(mesh.material as THREE.MeshStandardMaterial).emissiveIntensity = 0.25
      })
      return
    }
    const neighborIds = new Set<string>()
    edgesRef.current.forEach(e => {
      if (e.source === hovId) neighborIds.add(e.target)
      if (e.target === hovId) neighborIds.add(e.source)
    })
    meshMapRef.current.forEach((mesh, id) => {
      const mat = mesh.material as THREE.MeshStandardMaterial
      if (id === hovId) {
        mat.opacity = 1.0
        mat.emissiveIntensity = 0.55
        mesh.scale.setScalar(1.3)
      } else if (neighborIds.has(id)) {
        mat.opacity = 0.85
        mat.emissiveIntensity = 0.15
        mesh.scale.setScalar(1.0)
      } else {
        mat.opacity = 0.12
        mat.emissiveIntensity = 0.05
        mesh.scale.setScalar(1.0)
      }
    })
  }, [])

  /* ── Apply pulse for activeNodeIds (from WS memory_query) ─────────────── */

  useEffect(() => {
    if (activeNodeIds.size === 0) {
      // Reset if not hovering
      if (!hoveredIdRef.current) {
        meshMapRef.current.forEach(mesh => {
          const mat = mesh.material as THREE.MeshStandardMaterial
          mat.opacity = 0.9
          mat.emissiveIntensity = 0.25
          mesh.scale.setScalar(1.0)
        })
      }
      return
    }
    meshMapRef.current.forEach((mesh, id) => {
      const mat = mesh.material as THREE.MeshStandardMaterial
      if (activeNodeIds.has(id)) {
        mat.opacity = 1.0
        mat.emissiveIntensity = 0.85
        mesh.scale.setScalar(1.15)
      } else {
        mat.opacity = 0.15
        mat.emissiveIntensity = 0.0
        mesh.scale.setScalar(1.0)
      }
    })
  }, [activeNodeIds])

  /* ── Highlight selected node ───────────────────────────────────────────── */

  useEffect(() => {
    if (!selectedNodeId) return
    const mesh = meshMapRef.current.get(selectedNodeId)
    if (!mesh) return
    const mat = mesh.material as THREE.MeshStandardMaterial
    mat.opacity = 1.0
    mat.emissiveIntensity = 0.7
    mesh.scale.setScalar(1.2)
  }, [selectedNodeId])

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height: '100%', overflow: 'hidden' }}
    />
  )
}
