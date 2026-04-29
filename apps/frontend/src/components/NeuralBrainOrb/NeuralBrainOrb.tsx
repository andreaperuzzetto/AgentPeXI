/**
 * NeuralBrainOrb — Three.js WebGL renderer for the memory graph
 *
 * Architecture:
 *   WebGLRenderer + PerspectiveCamera + OrbitControls
 *   EffectComposer → RenderPass → UnrealBloomPass
 *   InstancedMesh (nodes) + LineSegments (edges)
 *   forceWorker (d3-force-3d in Web Worker) via Vite Worker import
 *
 * Data: GET /api/memory/graph?threshold=0.68
 * Voice state: read from uiStore.orbState → lerp ALL 6 params over ~1.2s (FE-2.3)
 * Pulse: memory_query WS events → per-node emissive flash via instanceColor
 * Interaction: raycaster on click → NodeDrawer
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import * as THREE from 'three'
import { OrbitControls }   from 'three/addons/controls/OrbitControls.js'
import { EffectComposer }  from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass }      from 'three/addons/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { useUiStore }      from '../../store/uiStore'
import { useStore }        from '../../store'
import { NodeDrawer, type GraphNode, type GraphEdge } from './NodeDrawer'

/* ── Constants ──────────────────────────────────────────────────────────── */

const BASE_RADIUS       = 0.5
const CONNECTION_SCALE  = 0.08
const MAX_CONNECTIONS   = 12
const LERP_FACTOR       = 0.04
const PULSE_DECAY       = 0.95
const PULSE_PEAK        = 2.5

const ZONE_COLOR_HEX: Record<string, number> = {
  // Zone identifiers used by the UI router
  neural:    0xB57BFF,
  etsy:      0xF5A623,
  personal:  0x1BFF5E,
  system:    0x8B8D98,
  analytics: 0xC8C8FF,
  // Zone values returned by /api/memory/graph (backend collection mapping)
  memory:    0xB57BFF,   // screen_memory (OCR) → neural purple (same family)
  shared:    0xC8C8FF,   // shared_memory (cross-domain bridge) → analytics lavender
}

interface VoiceParams {
  alphaTarget:    number
  charge:         number
  bloomStrength:  number
  bloomThreshold: number
  edgeOpacity:    number
  rotSpeed:       number
}

const VOICE_PARAMS: Record<string, VoiceParams> = {
  wakeword:  { alphaTarget: 0.01, charge: -25,  bloomStrength: 0.35, bloomThreshold: 0.85, edgeOpacity: 0.08, rotSpeed: 0.3 },
  listening: { alphaTarget: 0.12, charge: -45,  bloomStrength: 0.90, bloomThreshold: 0.60, edgeOpacity: 0.30, rotSpeed: 0.8 },
  thinking:  { alphaTarget: 0.45, charge: -85,  bloomStrength: 1.80, bloomThreshold: 0.28, edgeOpacity: 0.70, rotSpeed: 1.8 },
  speaking:  { alphaTarget: 0.20, charge: -50,  bloomStrength: 1.20, bloomThreshold: 0.45, edgeOpacity: 0.45, rotSpeed: 1.2 },
}

/* ── Mock data for DEV fallback ─────────────────────────────────────────── */

const MOCK_NODES: GraphNode[] = [
  { id: 'm1',  label: 'summer tote bag design',      collection: 'design_artifacts',  zone: 'etsy'      },
  { id: 'm2',  label: 'color theory warm palette',   collection: 'research_cache',    zone: 'etsy'      },
  { id: 'm3',  label: 'competitor price scan',        collection: 'market_intel',      zone: 'etsy'      },
  { id: 'm4',  label: 'morning routine context',      collection: 'personal_context',  zone: 'personal'  },
  { id: 'm5',  label: 'api cost optimization',        collection: 'system_knowledge',  zone: 'system'    },
  { id: 'm6',  label: 'ctr test results july',        collection: 'analytics_store',   zone: 'analytics' },
  { id: 'm7',  label: 'bohemian niche keywords',      collection: 'market_intel',      zone: 'etsy'      },
  { id: 'm8',  label: 'neural orb architecture',      collection: 'system_knowledge',  zone: 'neural'    },
  { id: 'm9',  label: 'weekly revenue pattern',       collection: 'analytics_store',   zone: 'analytics' },
  { id: 'm10', label: 'bundle threshold logic',       collection: 'system_knowledge',  zone: 'etsy'      },
]

const MOCK_EDGES = [
  { source: 'm1', target: 'm2', weight: 0.82 },
  { source: 'm1', target: 'm7', weight: 0.75 },
  { source: 'm2', target: 'm7', weight: 0.69 },
  { source: 'm3', target: 'm7', weight: 0.71 },
  { source: 'm3', target: 'm9', weight: 0.68 },
  { source: 'm4', target: 'm8', weight: 0.73 },
  { source: 'm5', target: 'm8', weight: 0.90 },
  { source: 'm5', target: 'm10', weight: 0.85 },
  { source: 'm6', target: 'm9', weight: 0.78 },
  { source: 'm6', target: 'm3', weight: 0.70 },
  { source: 'm10', target: 'm1', weight: 0.72 },
]

/* ── Component ──────────────────────────────────────────────────────────── */

export function NeuralBrainOrb() {
  const containerRef = useRef<HTMLDivElement>(null)

  /* Node drawer */
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const nodesRef = useRef<GraphNode[]>([])
  const graphEdgesRef = useRef<GraphEdge[]>([])

  /* Three.js objects exposed for raycasting */
  const instancedMeshRef = useRef<THREE.InstancedMesh | null>(null)
  const cameraRef        = useRef<THREE.PerspectiveCamera | null>(null)

  /* Node-ID → index map for pulse lookup */
  const nodeIndexRef = useRef<Map<string, number>>(new Map())

  /* Per-node pulse intensity (decays each frame) */
  const pulseRef = useRef<Float32Array | null>(null)

  /* Worker reference — needed to send update_params from outside the main effect */
  const workerRef = useRef<Worker | null>(null)

  /* Voice-state lerp: ALL 6 params — bloom, edge, rotation + physics (alphaTarget, charge) */
  const lerpCurrentRef = useRef<VoiceParams>({ ...VOICE_PARAMS['wakeword'] })
  const lerpTargetRef  = useRef<VoiceParams>({ ...VOICE_PARAMS['wakeword'] })

  /* Last processed memory-query timestamp — dedup */
  const lastPulseTsRef = useRef(0)

  /* Zustand selectors */
  const orbState       = useUiStore(s => s.orbState)
  const memoryQueryFeed = useStore(s => s.memoryQueryFeed)

  /* ── Voice state change: update lerp target only.
       Physics params (alphaTarget, charge) are sent to the worker gradually
       each RAF frame as they lerp — no discrete jump. ── */
  useEffect(() => {
    const params = VOICE_PARAMS[orbState] ?? VOICE_PARAMS['wakeword']
    lerpTargetRef.current = { ...params }
  }, [orbState])

  /* ── Memory query pulse: highlight queried nodes ── */
  useEffect(() => {
    if (memoryQueryFeed.length === 0 || !pulseRef.current) return
    const latest = memoryQueryFeed[memoryQueryFeed.length - 1]
    if (latest.ts <= lastPulseTsRef.current) return
    lastPulseTsRef.current = latest.ts
    latest.ids.forEach(id => {
      const idx = nodeIndexRef.current.get(id)
      if (idx !== undefined && pulseRef.current) {
        pulseRef.current[idx] = PULSE_PEAK
      }
    })
  }, [memoryQueryFeed])

  /* ── Main Three.js setup ── */
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    /* ── Renderer ── */
    const renderer = new THREE.WebGLRenderer({
      antialias:              true,
      alpha:                  true,
      logarithmicDepthBuffer: true,
    })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(container.clientWidth, container.clientHeight)
    renderer.toneMapping = THREE.ACESFilmicToneMapping
    container.appendChild(renderer.domElement)

    /* ── Scene ── */
    const scene = new THREE.Scene()

    /* ── Camera ── */
    const camera = new THREE.PerspectiveCamera(60, container.clientWidth / container.clientHeight, 0.1, 1000)
    camera.position.set(0, 0, 60)
    cameraRef.current = camera

    /* ── Lights ── */
    scene.add(new THREE.AmbientLight(0xffffff, 0.4))
    const pointLight = new THREE.PointLight(0xffffff, 1.2, 300)
    scene.add(pointLight)

    /* ── OrbitControls ── */
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping  = true
    controls.dampingFactor  = 0.05
    controls.autoRotate     = true
    controls.autoRotateSpeed = VOICE_PARAMS['wakeword'].rotSpeed

    /* ── EffectComposer + Bloom ── */
    const composer  = new EffectComposer(renderer)
    const renderPass = new RenderPass(scene, camera)
    composer.addPass(renderPass)

    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(container.clientWidth, container.clientHeight),
      VOICE_PARAMS['wakeword'].bloomStrength,
      0.4,
      VOICE_PARAMS['wakeword'].bloomThreshold,
    )
    composer.addPass(bloomPass)

    /* ── RAF state ── */
    let rafId   = 0
    const dummy = new THREE.Object3D()
    const tempColor  = new THREE.Color()
    const whiteColor = new THREE.Color(0xffffff)

    /* Latest worker positions */
    let workerPositions: Float32Array | null = null

    /* Last physics values sent to worker — used to gate incremental sends */
    let lastSentAlpha  = VOICE_PARAMS['wakeword'].alphaTarget
    let lastSentCharge = VOICE_PARAMS['wakeword'].charge

    /* Mutable node-level data (set once in initGraph, read in RAF) */
    let nodeScales:  Float32Array = new Float32Array(0)
    let nodeColors:  THREE.Color[] = []
    let nodeCount   = 0
    let edgeCount   = 0
    let edgeSources: Uint32Array = new Uint32Array(0)
    let edgeTargets: Uint32Array = new Uint32Array(0)

    /* Cached scene objects — set in initGraph, read in RAF (avoids O(n) find per frame) */
    let lineSegments:   THREE.LineSegments    | null = null
    let lineMaterial:   THREE.LineBasicMaterial | null = null
    let edgePosAttr:    THREE.BufferAttribute   | null = null

    /* ── Build scene from graph data ── */
    function initGraph(graphNodes: GraphNode[], graphEdges: GraphEdge[]) {
      /* Build index map */
      const indexMap = new Map<string, number>()
      graphNodes.forEach((n, i) => indexMap.set(n.id, i))
      nodeIndexRef.current = indexMap

      /* Connection count per node for radius scaling.
         Prefer the backend's pre-computed value (node.connections) when available
         (the graph endpoint already computes this). Falls back to counting edges locally. */
      const connCount = new Uint32Array(graphNodes.length)
      const backendHasConnections = graphNodes.length > 0 && graphNodes[0].connections !== undefined
      if (backendHasConnections) {
        graphNodes.forEach((n, i) => { connCount[i] = n.connections ?? 0 })
      } else {
        graphEdges.forEach(e => {
          const si = indexMap.get(e.source)
          const ti = indexMap.get(e.target)
          if (si !== undefined) connCount[si]++
          if (ti !== undefined) connCount[ti]++
        })
      }

      nodeCount = graphNodes.length
      edgeCount = graphEdges.length
      nodeScales = new Float32Array(nodeCount)
      nodeColors = []

      /* ── InstancedMesh for nodes ── */
      const sphereGeo = new THREE.SphereGeometry(1, 12, 8)
      const nodeMat = new THREE.MeshStandardMaterial({
        metalness:  0.1,
        roughness:  0.7,
        toneMapped: false,
      })
      const mesh = new THREE.InstancedMesh(sphereGeo, nodeMat, nodeCount)
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage)

      graphNodes.forEach((node, i) => {
        const r = BASE_RADIUS + Math.min(connCount[i], MAX_CONNECTIONS) * CONNECTION_SCALE
        nodeScales[i] = r

        /* Scatter randomly — worker will converge positions */
        dummy.position.set(
          (Math.random() - 0.5) * 40,
          (Math.random() - 0.5) * 40,
          (Math.random() - 0.5) * 40,
        )
        dummy.scale.setScalar(r)
        dummy.updateMatrix()
        mesh.setMatrixAt(i, dummy.matrix)

        const c = new THREE.Color(ZONE_COLOR_HEX[node.zone] ?? ZONE_COLOR_HEX['neural'])
        mesh.setColorAt(i, c)
        nodeColors.push(c)
      })
      mesh.instanceMatrix.needsUpdate = true
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true

      scene.add(mesh)
      instancedMeshRef.current = mesh
      nodesRef.current      = graphNodes
      graphEdgesRef.current = graphEdges

      /* Pulse intensity array */
      pulseRef.current = new Float32Array(nodeCount)

      /* ── Edge index arrays (source/target indices) ── */
      edgeSources = new Uint32Array(edgeCount)
      edgeTargets = new Uint32Array(edgeCount)
      graphEdges.forEach((e, i) => {
        edgeSources[i] = indexMap.get(e.source) ?? 0
        edgeTargets[i] = indexMap.get(e.target) ?? 0
      })

      /* ── LineSegments for edges ── */
      const edgePositions = new Float32Array(edgeCount * 2 * 3)
      const edgeGeo = new THREE.BufferGeometry()
      edgeGeo.setAttribute(
        'position',
        new THREE.BufferAttribute(edgePositions, 3).setUsage(THREE.DynamicDrawUsage),
      )
      const edgeMat = new THREE.LineBasicMaterial({
        color:       0xffffff,
        transparent: true,
        opacity:     VOICE_PARAMS['wakeword'].edgeOpacity,
        toneMapped:  false,
      })
      const lines = new THREE.LineSegments(edgeGeo, edgeMat)
      scene.add(lines)

      /* Cache for O(1) access in the RAF loop */
      lineSegments = lines
      lineMaterial = edgeMat
      edgePosAttr  = edgeGeo.getAttribute('position') as THREE.BufferAttribute

      /* ── Start forceWorker ── */
      const worker = new Worker(
        new URL('../../workers/forceWorker.ts', import.meta.url),
        { type: 'module' },
      )
      worker.onmessage = ({ data }: MessageEvent<{ positions: Float32Array }>) => {
        workerPositions = data.positions
      }
      worker.postMessage({
        type:  'init',
        nodes: graphNodes.map(n => ({ id: n.id, zone: n.zone })),
        edges: graphEdges.map(e => ({ source: e.source, target: e.target })),
        params: {
          alphaTarget: lerpTargetRef.current.alphaTarget,
          charge:      lerpTargetRef.current.charge,
        },
      })
      workerRef.current = worker
    }

    /* ── Fetch graph data ── */
    fetch('/api/memory/graph?threshold=0.68')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`graph fetch ${r.status}`)))
      .then((data: { nodes: GraphNode[]; edges: GraphEdge[] }) => {
        initGraph(data.nodes ?? [], data.edges ?? [])
      })
      .catch(() => {
        if (import.meta.env.DEV) {
          initGraph(MOCK_NODES, MOCK_EDGES)
        }
      })

    /* ── Animate loop ── */
    function animate() {
      rafId = requestAnimationFrame(animate)

      const lerp   = lerpCurrentRef.current
      const target = lerpTargetRef.current

      /* Lerp ALL 6 voice-state params toward target (~1.2s @ 60fps with factor 0.04) */
      lerp.alphaTarget    += (target.alphaTarget    - lerp.alphaTarget)    * LERP_FACTOR
      lerp.charge         += (target.charge         - lerp.charge)         * LERP_FACTOR
      lerp.bloomStrength  += (target.bloomStrength  - lerp.bloomStrength)  * LERP_FACTOR
      lerp.bloomThreshold += (target.bloomThreshold - lerp.bloomThreshold) * LERP_FACTOR
      lerp.edgeOpacity    += (target.edgeOpacity    - lerp.edgeOpacity)    * LERP_FACTOR
      lerp.rotSpeed       += (target.rotSpeed       - lerp.rotSpeed)       * LERP_FACTOR

      bloomPass.strength       = lerp.bloomStrength
      bloomPass.threshold      = lerp.bloomThreshold
      controls.autoRotateSpeed = lerp.rotSpeed

      /* Send physics params to worker when they've drifted meaningfully.
         Thresholds: alphaTarget >0.002 | charge >0.5 → ~every few frames during a transition.
         Avoids flooding the worker with identical messages during steady state. */
      if (
        workerRef.current &&
        (Math.abs(lerp.alphaTarget - lastSentAlpha)  > 0.002 ||
         Math.abs(lerp.charge      - lastSentCharge) > 0.5)
      ) {
        workerRef.current.postMessage({
          type:   'update_params',
          params: { alphaTarget: lerp.alphaTarget, charge: lerp.charge },
        })
        lastSentAlpha  = lerp.alphaTarget
        lastSentCharge = lerp.charge
      }

      /* Apply worker positions to InstancedMesh + edge geometry */
      const mesh = instancedMeshRef.current
      const positions = workerPositions

      if (mesh && positions && positions.length === nodeCount * 3) {
        const pulse = pulseRef.current
        let colorNeedsUpdate = false

        /* Update edge opacity (lerped) + positions — uses cached refs, O(1) lookup */
        if (lineMaterial) lineMaterial.opacity = lerp.edgeOpacity

        if (lineSegments && edgePosAttr) {
          const edgePosArr = edgePosAttr.array as Float32Array
          for (let ei = 0; ei < edgeCount; ei++) {
            const si = edgeSources[ei]
            const ti = edgeTargets[ei]
            edgePosArr[ei * 6]     = positions[si * 3]
            edgePosArr[ei * 6 + 1] = positions[si * 3 + 1]
            edgePosArr[ei * 6 + 2] = positions[si * 3 + 2]
            edgePosArr[ei * 6 + 3] = positions[ti * 3]
            edgePosArr[ei * 6 + 4] = positions[ti * 3 + 1]
            edgePosArr[ei * 6 + 5] = positions[ti * 3 + 2]
          }
          edgePosAttr.needsUpdate = true
        }

        /* Update node matrices + pulse colors */
        for (let i = 0; i < nodeCount; i++) {
          dummy.position.set(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2])
          dummy.scale.setScalar(nodeScales[i])
          dummy.updateMatrix()
          mesh.setMatrixAt(i, dummy.matrix)

          /* Pulse decay and color flash */
          if (pulse && pulse[i] > 0.01) {
            pulse[i] *= PULSE_DECAY
            const t = Math.min(pulse[i] / PULSE_PEAK, 1)
            tempColor.copy(nodeColors[i]).lerp(whiteColor, t)
            mesh.setColorAt(i, tempColor)
            colorNeedsUpdate = true
          }
        }

        mesh.instanceMatrix.needsUpdate = true
        if (colorNeedsUpdate && mesh.instanceColor) {
          mesh.instanceColor.needsUpdate = true
        }
      }

      controls.update()
      composer.render()
    }

    animate()

    /* ── Resize observer ── */
    const ro = new ResizeObserver(() => {
      const w = container.clientWidth
      const h = container.clientHeight
      if (w === 0 || h === 0) return
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)
      composer.setSize(w, h)
      bloomPass.setSize(w, h)
    })
    ro.observe(container)

    /* ── Cleanup ── */
    return () => {
      cancelAnimationFrame(rafId)
      ro.disconnect()

      workerRef.current?.postMessage({ type: 'stop' })
      workerRef.current?.terminate()
      workerRef.current = null

      instancedMeshRef.current = null
      cameraRef.current = null
      lineSegments = null
      lineMaterial = null
      edgePosAttr  = null

      controls.dispose()
      bloomPass.dispose()
      renderPass.dispose()
      composer.dispose()

      scene.traverse(obj => {
        const mesh3 = obj as THREE.Mesh
        if (mesh3.geometry) mesh3.geometry.dispose()
        if (mesh3.material) {
          if (Array.isArray(mesh3.material)) mesh3.material.forEach(m => m.dispose())
          else mesh3.material.dispose()
        }
      })

      renderer.dispose()
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ── Raycaster click handler ── */
  const handleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const mesh   = instancedMeshRef.current
    const camera = cameraRef.current
    const container = containerRef.current
    if (!mesh || !camera || !container) return

    const rect  = container.getBoundingClientRect()
    const mouse = new THREE.Vector2(
       ((e.clientX - rect.left) / rect.width)  * 2 - 1,
      -((e.clientY - rect.top)  / rect.height) * 2 + 1,
    )

    const raycaster = new THREE.Raycaster()
    raycaster.params.Points = { threshold: 0.5 }
    raycaster.setFromCamera(mouse, camera)

    const hits = raycaster.intersectObject(mesh)
    if (hits.length > 0 && hits[0].instanceId !== undefined) {
      const node = nodesRef.current[hits[0].instanceId]
      if (node) {
        setSelectedNodeId(prev => prev === node.id ? null : node.id)
        return
      }
    }
    setSelectedNodeId(null)
  }, [])

  /* ── Render ── */
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      {/* Canvas container — radial violet atmosphere (4% opacity) */}
      <div
        ref={containerRef}
        onClick={handleClick}
        style={{
          width:  '100%',
          height: '100%',
          background: 'radial-gradient(ellipse at 50% 60%, rgba(181,123,255,0.04) 0%, #0B0C0F 65%)',
          cursor: 'default',
          overflow: 'hidden',
        }}
      />

      {/* Node detail drawer — absolute, over canvas */}
      <NodeDrawer
        nodeId={selectedNodeId}
        nodes={nodesRef.current}
        edges={graphEdgesRef.current}
        onClose={() => setSelectedNodeId(null)}
      />
    </div>
  )
}
