/* ═══════════════════════════════════════════════════════════════════
   NeuralBrain — Canvas 2D memory graph + particle orb
   Real data from /api/memory/graph • Live events via WS memory_query
   Orb state driven by uiStore (voice pipeline)
═══════════════════════════════════════════════════════════════════ */
import { useRef, useEffect, useState, useCallback } from 'react'
import { useUiStore, type OrbState } from '../../store/uiStore'
import { useStore } from '../../store'
import './NeuralBrain.css'

// ── Palette (matches --zone-* and --acc in globals.css) ─────────
const C = {
  bg:     '#000000',
  acc:    '#1BFF5E',   // green  — personal zone + orb primary
  blue:   '#7EB8FF',   // blue   — orb thinking state
  amber:  '#F5A623',   // amber  — etsy zone
  purple: '#B57BFF',   // purple — memory zone (screen OCR)
  shared: '#C8C8FF',   // lavender — shared_memory cross-domain bridge
} as const

const ZONE_COLOR: Record<string, string> = {
  etsy:     C.amber,
  personal: C.acc,
  memory:   C.purple,
  shared:   C.shared,  // bridge nodes — attraversano entrambe le zone
}

// ── Canvas orb states ────────────────────────────────────────────
type CanvasOrbState = 'off' | 'waking' | 'listen' | 'process' | 'think' | 'sleeping'
type OrbPKey = 'listen' | 'process' | 'think'

// ── Data models ──────────────────────────────────────────────────
interface GNode {
  id: string; label: string; collection: string; zone: string
  document: string; metadata: Record<string, unknown>; connections: number
  x: number; y: number; vx: number; vy: number
  glow: number   // 0–1, fades over 3.5s after memory_query activation
}

interface GEdge { source: string; target: string; weight: number }

interface OrbP {
  ang: number; spd: number; r: number; sz: number
  dir: number; wobA: number; wobSpd: number; wobR: number; col: string
}

interface Stream {
  life: number; maxLife: number
  targetNodeId: string
  perpOff: { x: number; y: number }   // stable bezier spread — set at spawn
  col: string; spd: number
  returning: boolean; bezierT: number
}

interface NodeDetail {
  id: string; document: string
  metadata: Record<string, unknown>; collection: string
  access_history: Array<{ agent: string; query_text: string | null; queried_at: string }>
}

// ── Orb visual parameters ────────────────────────────────────────
const ORB_P: Record<OrbPKey, { sM: number; rM: number; aM: number; bA: number; bS: number; hue: string }> = {
  listen:  { sM: 1.0,  rM: 1.0,  aM: 1.0,  bA: 4,  bS: 1.1,  hue: C.acc  },
  process: { sM: 2.8,  rM: 0.88, aM: 1.35, bA: 13, bS: 3.2,  hue: C.acc  },
  think:   { sM: 0.35, rM: 1.12, aM: 0.82, bA: 7,  bS: 0.55, hue: C.blue },
}

// ── Physics constants ────────────────────────────────────────────
const REPULSION  = 6000
const SPRING_K   = 0.0015
const DAMPING    = 0.86
const MAX_SPD    = 3
const IDEAL_LEN  = 200
const CTR_PULL   = 0.00035

// ── Mapping: uiStore orbState → canvas state ─────────────────────
const UI_CANVAS: Record<OrbState, CanvasOrbState> = {
  wakeword:  'off',
  listening: 'listen',
  thinking:  'think',
  speaking:  'process',
}

// ── Utility ──────────────────────────────────────────────────────
function rgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${Math.min(1, Math.max(0, a)).toFixed(3)})`
}

function mkParticles(): OrbP[] {
  return Array.from({ length: 220 }, () => {
    const rr = Math.random()
    return {
      ang:    Math.random() * Math.PI * 2,
      spd:    0.004 + Math.random() * 0.016,
      r:      38 + Math.random() * 54,
      sz:     1.2 + Math.random() * 2.2,
      dir:    Math.random() < 0.5 ? 1 : -1,
      wobA:   Math.random() * Math.PI * 2,
      wobSpd: 0.3 + Math.random() * 0.9,
      wobR:   4 + Math.random() * 12,
      col:    rr < 0.55 ? C.acc : rr < 0.75 ? C.blue : rr < 0.88 ? C.amber : C.purple,
    }
  })
}

// ── Top-level draw helpers (no closure over React state) ─────────
function drawOrb(
  ctx: CanvasRenderingContext2D,
  state: CanvasOrbState, transT: number, t: number,
  osx: number, osy: number, zoom: number,
  ps: OrbP[], paramKey: OrbPKey,
) {
  if (state === 'off' || transT <= 0.01) return
  const p = ORB_P[paramKey]
  const breath = Math.sin(t * p.bS) * p.bA
  const pulseR = state === 'waking' ? 20 * (1 - transT) : 0
  const tS = transT

  // Core glow
  const cR = (20 + pulseR) * tS * zoom
  const rg = ctx.createRadialGradient(osx, osy, 0, osx, osy, cR * 3)
  rg.addColorStop(0, rgba(p.hue, 0.18 * p.aM * tS))
  rg.addColorStop(1, 'rgba(0,0,0,0)')
  ctx.beginPath(); ctx.fillStyle = rg; ctx.arc(osx, osy, cR * 3, 0, Math.PI * 2); ctx.fill()

  // Particles
  for (const part of ps) {
    const rEff = (part.r * p.rM + breath + pulseR) * tS * zoom
    const px = osx + Math.cos(part.ang) * rEff + Math.sin(part.wobA) * part.wobR * tS * zoom
    const py = osy + Math.sin(part.ang) * rEff + Math.cos(part.wobA) * part.wobR * tS * zoom
    ctx.beginPath()
    ctx.arc(px, py, part.sz * tS * zoom, 0, Math.PI * 2)
    ctx.fillStyle = rgba(part.col, 0.55 * p.aM * tS)
    ctx.fill()
  }
}

function drawStreams(
  ctx: CanvasRenderingContext2D,
  streams: Stream[],
  osx: number, osy: number,
  nodeMap: Map<string, GNode>,
  pan: { x: number; y: number }, zoom: number,
) {
  for (const s of streams) {
    const node = nodeMap.get(s.targetNodeId); if (!node) continue
    const nx = node.x * zoom + pan.x, ny = node.y * zoom + pan.y
    const p0 = s.returning ? { x: nx, y: ny }   : { x: osx, y: osy }
    const p2 = s.returning ? { x: osx, y: osy } : { x: nx, y: ny }
    const mx = (p0.x + p2.x) / 2 + s.perpOff.x
    const my = (p0.y + p2.y) / 2 + s.perpOff.y
    const bt = s.bezierT
    const bx = (1-bt)*(1-bt)*p0.x + 2*(1-bt)*bt*mx + bt*bt*p2.x
    const by = (1-bt)*(1-bt)*p0.y + 2*(1-bt)*bt*my + bt*bt*p2.y
    const alpha = Math.sin(bt * Math.PI) * 0.85
    ctx.beginPath(); ctx.arc(bx, by, 2.5 * zoom, 0, Math.PI * 2)
    ctx.fillStyle = rgba(s.col, alpha); ctx.fill()
    // Trail dots
    for (let i = 1; i <= 3; i++) {
      const tbt = Math.max(0, bt - i * 0.035)
      const tbx = (1-tbt)*(1-tbt)*p0.x + 2*(1-tbt)*tbt*mx + tbt*tbt*p2.x
      const tby = (1-tbt)*(1-tbt)*p0.y + 2*(1-tbt)*tbt*my + tbt*tbt*p2.y
      ctx.beginPath(); ctx.arc(tbx, tby, Math.max(0.5, (2.5 - i * 0.6)) * zoom, 0, Math.PI * 2)
      ctx.fillStyle = rgba(s.col, alpha * (1 - i / 4) * 0.35); ctx.fill()
    }
  }
}

// ── Component ─────────────────────────────────────────────────────
export function NeuralBrain() {
  const canvasRef   = useRef<HTMLCanvasElement>(null)
  const rafRef      = useRef<number>(0)
  const t0Ref       = useRef(performance.now())

  // Graph state (all refs — no re-renders from canvas loop)
  const nodesRef       = useRef<GNode[]>([])
  const edgesRef       = useRef<GEdge[]>([])
  const nodeMapRef     = useRef<Map<string, GNode>>(new Map())
  const panRef         = useRef({ x: 0, y: 0 })
  const zoomRef        = useRef(0.88)
  const isPanRef       = useRef(false)
  const panStartRef    = useRef({ x: 0, y: 0, px: 0, py: 0 })
  const loadingRef     = useRef(true)
  const draggedNodeRef = useRef<GNode | null>(null)
  const didDragRef     = useRef(false)

  // Orb state refs
  const orbStateRef  = useRef<CanvasOrbState>('off')
  const orbTargetRef = useRef<OrbPKey>('listen')
  const orbWXRef     = useRef(0)
  const orbWYRef     = useRef(0)
  const orbTRef      = useRef(0)        // 0=off, 1=fully awake
  const psRef        = useRef<OrbP[]>(mkParticles())
  const streamsRef   = useRef<Stream[]>([])
  const prevUiRef    = useRef<OrbState>('wakeword')

  // Activated nodes from WS memory_query events
  const activatedRef = useRef<Map<string, number>>(new Map())  // id → timestamp

  // React state — only for NodeDrawer (triggers re-render)
  const [selectedNode, setSelectedNode] = useState<GNode | null>(null)
  const [nodeDetail,   setNodeDetail]   = useState<NodeDetail | null>(null)

  // Store subscriptions
  const uiOrbState      = useUiStore((s) => s.orbState)
  const lastMemoryQuery = useStore((s) => s.lastMemoryQuery)

  // ── Coordinate helper ─────────────────────────────────────────
  const toWorld = useCallback((sx: number, sy: number) => ({
    x: (sx - panRef.current.x) / zoomRef.current,
    y: (sy - panRef.current.y) / zoomRef.current,
  }), [])

  // ── Fetch graph data on mount ─────────────────────────────────
  useEffect(() => {
    let dead = false

    const initGraph = (
      rawNodes: Array<{ id: string; label: string; collection: string; zone: string; document: string; metadata: Record<string, unknown>; connections: number }>,
      rawEdges: GEdge[],
    ) => {
      if (dead) return
      const canvas = canvasRef.current
      const W = canvas?.clientWidth  ?? 900
      const H = canvas?.clientHeight ?? 560
      const R = Math.min(W, H) * 0.34

      const nodes: GNode[] = rawNodes.map((n, i) => {
        const angle  = (i / Math.max(rawNodes.length, 1)) * Math.PI * 2
        const jitter = 0.75 + Math.random() * 0.5
        return {
          ...n,
          x: W / 2 + Math.cos(angle) * R * jitter,
          y: H / 2 + Math.sin(angle) * R * jitter,
          vx: 0, vy: 0, glow: 0,
        }
      })

      nodesRef.current   = nodes
      edgesRef.current   = rawEdges
      nodeMapRef.current = new Map(nodes.map((n) => [n.id, n]))
      loadingRef.current = false
    }

    const mockFallback = () => {
      const defs = [
        { zone: 'etsy',     label: 'Trend Etsy',    col: 'pepe_memory'   },
        { zone: 'etsy',     label: 'Analytics Q1',  col: 'pepe_memory'   },
        { zone: 'etsy',     label: 'Listing data',  col: 'pepe_memory'   },
        { zone: 'personal', label: 'Reminder',      col: 'screen_memory' },
        { zone: 'personal', label: 'Research',      col: 'screen_memory' },
        { zone: 'memory',   label: 'Schermata',     col: 'screen_memory' },
        { zone: 'memory',   label: 'Appunti',       col: 'screen_memory' },
        { zone: 'memory',   label: 'Sessione',      col: 'screen_memory' },
      ]
      initGraph(
        defs.map((d, i) => ({
          id: `mock-${i}`, label: d.label, collection: d.col,
          zone: d.zone, document: 'Dati non disponibili (backend offline)',
          metadata: {}, connections: Math.floor(Math.random() * 4),
        })),
        [
          { source: 'mock-0', target: 'mock-1', weight: 0.82 },
          { source: 'mock-1', target: 'mock-2', weight: 0.74 },
          { source: 'mock-3', target: 'mock-4', weight: 0.78 },
          { source: 'mock-5', target: 'mock-6', weight: 0.71 },
        ],
      )
    }

    fetch('/api/memory/graph?threshold=0.68')
      .then((r) => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d) => initGraph(d.nodes ?? [], d.edges ?? []))
      .catch(() => mockFallback())

    return () => { dead = true }
  }, [])

  // ── Sync orb visual state from uiStore ────────────────────────
  useEffect(() => {
    const prev = prevUiRef.current
    prevUiRef.current = uiOrbState

    const sleeping = orbStateRef.current === 'off' || orbStateRef.current === 'sleeping'
    const waking   = orbStateRef.current === 'waking'

    if (uiOrbState === 'wakeword') {
      // Transition to sleep only if currently active
      if (!sleeping) orbStateRef.current = 'sleeping'
    } else {
      // Determine target orb param
      const target: OrbPKey = uiOrbState === 'speaking' ? 'process'
                            : uiOrbState === 'thinking' ? 'think'
                            : 'listen'
      orbTargetRef.current = target

      if (sleeping && !waking) {
        // Wake orb at canvas center (world coords)
        const canvas = canvasRef.current
        if (canvas) {
          const w = toWorld(canvas.width / 2, canvas.height / 2)
          orbWXRef.current = w.x
          orbWYRef.current = w.y
        }
        orbStateRef.current = 'waking'
        orbTRef.current = 0
      } else if (!sleeping && !waking) {
        orbStateRef.current = target
      }
    }
  }, [uiOrbState, toWorld])

  // ── Live node activation from WS memory_query events ─────────
  useEffect(() => {
    if (!lastMemoryQuery) return
    const nowMs = Date.now()

    // Mark activated nodes for glow
    lastMemoryQuery.ids.forEach((id) => activatedRef.current.set(id, nowMs))

    // Spawn stream particles if orb is awake
    const st = orbStateRef.current
    if (st === 'off' || st === 'sleeping') return

    const newStreams: Stream[] = []
    lastMemoryQuery.ids.slice(0, 4).forEach((id) => {
      if (streamsRef.current.length + newStreams.length >= 8) return
      const node = nodeMapRef.current.get(id); if (!node) return
      const col   = ZONE_COLOR[node.zone] ?? C.acc
      const angle = Math.random() * Math.PI * 2
      const spread = 22 + Math.random() * 44
      newStreams.push({
        life: 0, maxLife: 75 + Math.floor(Math.random() * 40),
        targetNodeId: id,
        perpOff: { x: Math.cos(angle) * spread, y: Math.sin(angle) * spread },
        col, spd: 0.014 + Math.random() * 0.008,
        returning: false, bezierT: 0,
      })
    })
    streamsRef.current = [...streamsRef.current, ...newStreams]
  }, [lastMemoryQuery])

  // ── Canvas resize observer ────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return
    const ro = new ResizeObserver(() => {
      canvas.width  = canvas.clientWidth
      canvas.height = canvas.clientHeight
    })
    ro.observe(canvas)
    canvas.width  = canvas.clientWidth
    canvas.height = canvas.clientHeight
    return () => ro.disconnect()
  }, [])

  // ── Mouse / wheel events ──────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return

    const onDown = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      const w    = toWorld(e.clientX - rect.left, e.clientY - rect.top)
      const HR   = 18 / zoomRef.current

      // Hit-test nodes first — if we land on one, drag it instead of panning
      let hit: GNode | null = null
      for (const n of nodesRef.current) {
        const dx = n.x - w.x, dy = n.y - w.y
        if (dx * dx + dy * dy < HR * HR) { hit = n; break }
      }

      didDragRef.current = false
      panStartRef.current = { x: e.clientX, y: e.clientY, px: panRef.current.x, py: panRef.current.y }

      if (hit) {
        draggedNodeRef.current = hit
        isPanRef.current = false
        // Pin velocity so physics doesn't fight the drag
        hit.vx = 0; hit.vy = 0
      } else {
        draggedNodeRef.current = null
        isPanRef.current = true
      }
    }
    const onMove = (e: MouseEvent) => {
      const dx = Math.abs(e.clientX - panStartRef.current.x)
      const dy = Math.abs(e.clientY - panStartRef.current.y)
      if (dx > 3 || dy > 3) didDragRef.current = true

      if (draggedNodeRef.current) {
        // Move the node in world space
        const rect = canvas.getBoundingClientRect()
        const w = toWorld(e.clientX - rect.left, e.clientY - rect.top)
        const n = draggedNodeRef.current
        n.x = w.x; n.y = w.y
        n.vx = 0;  n.vy = 0
        return
      }
      if (!isPanRef.current) return
      panRef.current = {
        x: panStartRef.current.px + (e.clientX - panStartRef.current.x),
        y: panStartRef.current.py + (e.clientY - panStartRef.current.y),
      }
    }
    const onUp = (e: MouseEvent) => {
      if (draggedNodeRef.current) {
        // Short tap on a node (no real drag) → open detail
        if (!didDragRef.current) handleCanvasClick(e)
        draggedNodeRef.current = null
        didDragRef.current = false
        return
      }
      isPanRef.current = false
      if (!didDragRef.current) handleCanvasClick(e)
      didDragRef.current = false
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = canvas.getBoundingClientRect()
      const mx = e.clientX - rect.left, my = e.clientY - rect.top
      const delta = e.deltaY > 0 ? 0.92 : 1.09
      const nz = Math.max(0.18, Math.min(5, zoomRef.current * delta))
      panRef.current = {
        x: mx - (mx - panRef.current.x) * (nz / zoomRef.current),
        y: my - (my - panRef.current.y) * (nz / zoomRef.current),
      }
      zoomRef.current = nz
    }
    const onLeave = () => { isPanRef.current = false }

    canvas.addEventListener('mousedown', onDown)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    canvas.addEventListener('mouseleave', onLeave)
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      canvas.removeEventListener('mousedown', onDown)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      canvas.removeEventListener('mouseleave', onLeave)
      canvas.removeEventListener('wheel', onWheel)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleCanvasClick(e: MouseEvent) {
    const canvas = canvasRef.current; if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const w = toWorld(e.clientX - rect.left, e.clientY - rect.top)
    const HR = 18 / zoomRef.current
    for (const n of nodesRef.current) {
      const dx = n.x - w.x, dy = n.y - w.y
      if (dx * dx + dy * dy < HR * HR) {
        setSelectedNode(n)
        setNodeDetail(null)
        fetch(`/api/memory/node/${encodeURIComponent(n.id)}?collection=${n.collection}`)
          .then((r) => r.ok ? r.json() : null)
          .then((d) => d && setNodeDetail(d))
          .catch(() => {})
        return
      }
    }
    setSelectedNode(null)
  }

  // ── RAF animation loop ────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return

    function tick(now: number) {
      rafRef.current = requestAnimationFrame(tick)
      const ctx = canvas!.getContext('2d'); if (!ctx) return
      const W = canvas!.width, H = canvas!.height
      const t   = (now - t0Ref.current) * 0.001   // seconds
      const pan = panRef.current, zoom = zoomRef.current
      const nodes   = nodesRef.current
      const edges   = edgesRef.current
      const nodeMap = nodeMapRef.current

      // ─── Physics ───────────────────────────────────────────────
      // Repulsion (all pairs)
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i], b = nodes[j]
          const dx = b.x - a.x, dy = b.y - a.y
          const dSq = dx * dx + dy * dy + 1
          const d   = Math.sqrt(dSq)
          const f   = REPULSION / dSq
          a.vx -= dx / d * f; a.vy -= dy / d * f
          b.vx += dx / d * f; b.vy += dy / d * f
        }
      }
      // Edge springs
      for (const e of edges) {
        const a = nodeMap.get(e.source), b = nodeMap.get(e.target)
        if (!a || !b) continue
        const dx = b.x - a.x, dy = b.y - a.y
        const d  = Math.sqrt(dx * dx + dy * dy) + 0.01
        const f  = (d - IDEAL_LEN) * SPRING_K * e.weight
        a.vx += dx / d * f; a.vy += dy / d * f
        b.vx -= dx / d * f; b.vy -= dy / d * f
      }
      // Integration + center pull
      for (const n of nodes) {
        n.vx += (W / 2 - n.x) * CTR_PULL
        n.vy += (H / 2 - n.y) * CTR_PULL
        n.vx *= DAMPING; n.vy *= DAMPING
        const spd = Math.sqrt(n.vx * n.vx + n.vy * n.vy)
        if (spd > MAX_SPD) { n.vx = n.vx / spd * MAX_SPD; n.vy = n.vy / spd * MAX_SPD }
        n.x += n.vx; n.y += n.vy
        // Glow decay
        const ts = activatedRef.current.get(n.id)
        if (ts !== undefined) {
          n.glow = Math.max(0, 1 - (now - ts) / 3500)
          if (n.glow === 0) activatedRef.current.delete(n.id)
        } else {
          n.glow = 0
        }
      }

      // ─── Orb transitions ───────────────────────────────────────
      const orbSt = orbStateRef.current
      if (orbSt === 'waking') {
        orbTRef.current = Math.min(1, orbTRef.current + 0.022)
        if (orbTRef.current >= 1) orbStateRef.current = orbTargetRef.current
      } else if (orbSt === 'sleeping') {
        orbTRef.current = Math.max(0, orbTRef.current - 0.018)
        if (orbTRef.current <= 0) orbStateRef.current = 'off'
      }

      // Update orb particles
      const pk = ORB_P[orbTargetRef.current] ?? ORB_P.listen
      for (const p of psRef.current) {
        p.ang  += p.spd * p.dir * pk.sM
        p.wobA += p.wobSpd * 0.016
      }

      // Update streams
      streamsRef.current = streamsRef.current.filter((s) => {
        s.bezierT = Math.min(1, s.bezierT + s.spd)
        s.life++
        if (s.bezierT >= 1 && !s.returning) { s.returning = true; s.bezierT = 0 }
        return s.life < s.maxLife
      })

      // ─── Draw ──────────────────────────────────────────────────
      ctx.clearRect(0, 0, W, H)
      ctx.fillStyle = '#000'
      ctx.fillRect(0, 0, W, H)

      // World-space: graph (edges + nodes)
      ctx.save()
      ctx.setTransform(zoom, 0, 0, zoom, pan.x, pan.y)

      // Edges — gradient from source zone color to target zone color
      for (const e of edges) {
        const a = nodeMap.get(e.source), b = nodeMap.get(e.target)
        if (!a || !b) continue
        const colA     = ZONE_COLOR[a.zone] ?? C.acc
        const colB     = ZONE_COLOR[b.zone] ?? C.acc
        const edgeGlow = Math.max(a.glow, b.glow)
        const baseAlpha = 0.18 + e.weight * 0.28   // weight-based visibility
        const glowBoost = edgeGlow * 0.45

        const lg = ctx.createLinearGradient(a.x, a.y, b.x, b.y)
        lg.addColorStop(0, rgba(colA, baseAlpha + glowBoost))
        lg.addColorStop(1, rgba(colB, baseAlpha + glowBoost))

        ctx.beginPath()
        ctx.strokeStyle = lg
        ctx.lineWidth   = (0.9 + e.weight * 0.7 + edgeGlow * 0.8) / zoom
        ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke()
      }

      // Nodes — sphere appearance with radial gradient + specular highlight
      for (const n of nodes) {
        const col    = ZONE_COLOR[n.zone] ?? C.acc
        // World-space radius — scales with zoom (bigger when zoomed in, smaller when out)
        // Math.max clamps screen size to ≥2px so nodes never fully disappear
        const baseR  = 5.5 + Math.min(n.connections, 10) * 0.6  // world units
        const MIN_SCREEN_R = 2.0
        const r      = Math.max(MIN_SCREEN_R / zoom, baseR)       // world units, min 2px on screen
        const isDrag = draggedNodeRef.current === n
        const glow   = isDrag ? 1 : n.glow
        const active = glow > 0.04

        // ── Outer halo (glow bloom) ──────────────────────────────
        const haloR = r * (isDrag ? 8 : 5)
        const halo  = ctx.createRadialGradient(n.x, n.y, r * 0.8, n.x, n.y, haloR)
        halo.addColorStop(0, rgba(col, 0.22 + glow * 0.35))
        halo.addColorStop(0.55, rgba(col, 0.06 + glow * 0.10))
        halo.addColorStop(1, 'rgba(0,0,0,0)')
        ctx.beginPath(); ctx.fillStyle = halo
        ctx.arc(n.x, n.y, haloR, 0, Math.PI * 2); ctx.fill()

        // ── Sphere body — off-center radial gradient (3D illusion) ──
        // Light source: top-left at ~35% from center
        const hx = n.x - r * 0.33
        const hy = n.y - r * 0.33
        const sphere = ctx.createRadialGradient(hx, hy, r * 0.04, n.x, n.y, r)
        sphere.addColorStop(0,   rgba(col, 0.88 + glow * 0.12))   // bright highlight
        sphere.addColorStop(0.38, rgba(col, 0.72))                 // mid sphere
        sphere.addColorStop(0.75, rgba(col, 0.42 + glow * 0.15))  // dark edge tint
        sphere.addColorStop(1,   rgba(col, 0.16 + glow * 0.10))   // rim

        ctx.beginPath(); ctx.fillStyle = sphere
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill()

        // ── Specular highlight — small bright dot top-left ────────
        const sx = n.x - r * 0.28, sy = n.y - r * 0.28
        const spec = ctx.createRadialGradient(sx, sy, 0, sx, sy, r * 0.48)
        spec.addColorStop(0, `rgba(255,255,255,${(0.42 + glow * 0.25).toFixed(3)})`)
        spec.addColorStop(0.5, `rgba(255,255,255,${(0.10 + glow * 0.08).toFixed(3)})`)
        spec.addColorStop(1, 'rgba(255,255,255,0)')
        ctx.beginPath(); ctx.fillStyle = spec
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.fill()

        // ── Thin rim ring — gives crisp edge definition ───────────
        ctx.beginPath()
        ctx.strokeStyle = rgba(col, active ? 0.70 + glow * 0.30 : 0.38)
        ctx.lineWidth   = (0.8 + glow * 0.6) / zoom
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2); ctx.stroke()

      }

      ctx.restore()

      // ── Screen-space labels — fixed 11px, fade below zoom 0.38, never scale ──
      if (zoom > 0.30) {
        const lsz   = 11
        const alpha = Math.min(1, (zoom - 0.30) / 0.16)  // fade in 0.30→0.46
        ctx.font      = `500 ${lsz}px 'Space Grotesk', sans-serif`
        ctx.textAlign = 'center'
        for (const n of nodes) {
          const col       = ZONE_COLOR[n.zone] ?? C.acc
          const baseR     = 5.5 + Math.min(n.connections, 10) * 0.6
          const MIN_SR    = 2.0
          // actual screen radius mirrors the world-space calculation above
          const screenR   = Math.max(MIN_SR, baseR * zoom)
          const glow      = draggedNodeRef.current === n ? 1 : n.glow
          const active    = glow > 0.04
          const screenX   = n.x * zoom + pan.x
          const screenY   = n.y * zoom + pan.y
          const labelY    = screenY + screenR + lsz + 3   // 3px gap below sphere edge
          const textAlpha = active ? alpha : alpha * 0.62
          const lbl       = n.label.length > 26 ? n.label.slice(0, 24) + '…' : n.label

          ctx.fillStyle = active
            ? rgba(col, textAlpha * 0.92)
            : `rgba(184,212,188,${(textAlpha * 0.58).toFixed(3)})`
          ctx.fillText(lbl, screenX, labelY)
        }
      }

      // Screen-space: streams + orb (follow world position via zoom/pan)
      const osx = orbWXRef.current * zoom + pan.x
      const osy = orbWYRef.current * zoom + pan.y
      drawStreams(ctx, streamsRef.current, osx, osy, nodeMap, pan, zoom)
      drawOrb(ctx, orbStateRef.current, orbTRef.current, t, osx, osy, zoom, psRef.current, orbTargetRef.current)

      // Loading overlay
      if (loadingRef.current) {
        ctx.fillStyle = 'rgba(0,0,0,0.52)'
        ctx.fillRect(0, 0, W, H)
        ctx.fillStyle = 'rgba(27,255,94,0.5)'
        ctx.font      = '11px "JetBrains Mono", monospace'
        ctx.textAlign = 'center'
        ctx.fillText('caricamento grafo memoria…', W / 2, H / 2)
      }
    }

    rafRef.current = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(rafRef.current) }
  }, []) // empty deps — all state via refs

  return (
    <div className="neural-brain">
      <canvas ref={canvasRef} className="nb-canvas" />

      {selectedNode && (
        <NodeDrawer
          node={selectedNode}
          detail={nodeDetail}
          edges={edgesRef.current}
          nodeMap={nodeMapRef.current}
          onClose={() => { setSelectedNode(null); setNodeDetail(null) }}
        />
      )}
    </div>
  )
}

// ── Node Detail Drawer ────────────────────────────────────────────
interface DrawerProps {
  node:    GNode
  detail:  NodeDetail | null
  edges:   GEdge[]
  nodeMap: Map<string, GNode>
  onClose: () => void
}

function NodeDrawer({ node, detail, edges, nodeMap, onClose }: DrawerProps) {
  const col = ZONE_COLOR[node.zone] ?? C.acc

  const connectedNodes: GNode[] = edges
    .filter((e) => e.source === node.id || e.target === node.id)
    .map((e) => nodeMap.get(e.source === node.id ? e.target : e.source))
    .filter((n): n is GNode => n !== undefined)
    .slice(0, 8)

  return (
    <div className="nb-drawer">
      {/* ── Header ── */}
      <div className="nb-drawer-head">
        <span
          className="nb-drawer-zone"
          style={{ background: rgba(col, 0.12), borderColor: rgba(col, 0.32), color: col }}
        >
          {node.zone}
        </span>
        <span className="nb-drawer-label" title={node.label}>{node.label}</span>
        <button className="nb-drawer-close" onClick={onClose} title="Chiudi">✕</button>
      </div>

      {/* ── Body: left=history right=content ── */}
      <div className="nb-drawer-body">

        {/* Left panel: access history + connections */}
        <div className="nb-drawer-left">
          <div className="nb-section-lbl">Accessi ({detail?.access_history?.length ?? '…'})</div>
          <div className="nb-drawer-scroll">
            {!detail ? (
              <div className="nb-placeholder">caricamento…</div>
            ) : detail.access_history.length === 0 ? (
              <div className="nb-placeholder">nessun accesso registrato</div>
            ) : detail.access_history.map((h, i) => (
              <div key={i} className="nb-access-row">
                <span className="nb-access-agent">{h.agent}</span>
                {h.query_text && (
                  <span className="nb-access-query">
                    {h.query_text.length > 64 ? h.query_text.slice(0, 62) + '…' : h.query_text}
                  </span>
                )}
                <span className="nb-access-ts">
                  {new Date(h.queried_at).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            ))}
          </div>

          {connectedNodes.length > 0 && (
            <>
              <div className="nb-section-lbl" style={{ marginTop: 14 }}>
                Connessioni ({node.connections})
              </div>
              <div className="nb-chip-wrap">
                {connectedNodes.map((n) => {
                  const nc = ZONE_COLOR[n.zone] ?? C.acc
                  return (
                    <span
                      key={n.id}
                      className="nb-chip"
                      style={{ borderColor: rgba(nc, 0.38), color: nc }}
                    >
                      {n.label.slice(0, 22)}
                    </span>
                  )
                })}
              </div>
            </>
          )}
        </div>

        {/* Right panel: document + metadata */}
        <div className="nb-drawer-right">
          <div className="nb-section-lbl">
            Contenuto
            <span className="nb-coll-badge">{node.collection}</span>
          </div>

          <div className="nb-doc">
            {!detail
              ? <span className="nb-placeholder">caricamento…</span>
              : (detail.document || 'Nessun contenuto disponibile')}
          </div>

          {detail && Object.keys(detail.metadata).length > 0 && (
            <div className="nb-meta-block">
              {Object.entries(detail.metadata).slice(0, 6).map(([k, v]) => (
                <div key={k} className="nb-meta-row">
                  <span className="nb-meta-key">{k}</span>
                  <span className="nb-meta-val">{String(v).slice(0, 48)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
