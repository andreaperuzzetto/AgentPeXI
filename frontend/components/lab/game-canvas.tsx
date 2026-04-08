"use client"

import { useRef, useEffect, type RefObject } from "react"
import type { Agent, Deal } from "@/lib/lab-data"
import { BLACKBOARD_POS, WORKSTATION_TILES } from "@/lib/lab-data"
import {
  TILE_SIZE,
  MAP_COLS,
  MAP_ROWS,
  parseTiledJSON,
  isTileWalkable,
  tileToPixel,
} from "@/lib/tilemap"
import type { TilemapData, GridPos, TiledJSON } from "@/lib/tilemap"
import { initNPCState, transitionNPCMode, updateNPC } from "@/lib/npc-engine"
import type { NPCState } from "@/lib/npc-engine"

const DEBUG_GRID = false

interface GameCanvasProps {
  agents: Agent[]
  deal: Deal
  onAgentClick: (agent: Agent) => void
  onBlackboardClick: () => void
  mapRef: RefObject<HTMLDivElement | null>
}

export function GameCanvas({
  agents,
  deal,
  onAgentClick,
  onBlackboardClick,
  mapRef,
}: GameCanvasProps) {
  const tileCanvasRef = useRef<HTMLCanvasElement>(null)
  const npcCanvasRef = useRef<HTMLCanvasElement>(null)
  const tilemapRef = useRef<TilemapData | null>(null)
  const npcStatesRef = useRef<Map<string, NPCState>>(new Map())
  const agentsRef = useRef<Agent[]>(agents)
  const rafRef = useRef<number>(0)
  const lastTimestampRef = useRef<number>(0)
  const onAgentClickRef = useRef(onAgentClick)
  onAgentClickRef.current = onAgentClick

  // Sync agentsRef + lastAgentStatus nei ref senza re-render React
  useEffect(() => {
    agentsRef.current = agents
    for (const agent of agents) {
      const npc = npcStatesRef.current.get(agent.id)
      if (npc && npc.lastAgentStatus !== agent.status) {
        npcStatesRef.current.set(agent.id, { ...npc, lastAgentStatus: agent.status })
      }
    }
  }, [agents])

  // Mount: carica tilemap, init NPC, avvia game loop
  useEffect(() => {
    let mounted = true

    const tileCanvas = tileCanvasRef.current!
    const npcCanvas = npcCanvasRef.current!
    tileCanvas.width = MAP_COLS * TILE_SIZE
    tileCanvas.height = MAP_ROWS * TILE_SIZE
    npcCanvas.width = MAP_COLS * TILE_SIZE
    npcCanvas.height = MAP_ROWS * TILE_SIZE

    function randomWalkableTile(map: TilemapData): GridPos {
      for (let attempt = 0; attempt < 200; attempt++) {
        const col = 1 + Math.floor(Math.random() * (MAP_COLS - 2))
        const row = 2 + Math.floor(Math.random() * (MAP_ROWS - 5))
        if (isTileWalkable(map, col, row)) return { col, row }
      }
      // Fallback sicuro: centro mappa
      return { col: 7, row: 6 }
    }

    function buildFallbackMap(): TilemapData {
      const collision: (0 | 1 | 2)[][] = Array.from({ length: MAP_ROWS }, (_, r) =>
        Array.from(
          { length: MAP_COLS },
          (_, c): 0 | 1 | 2 => (r <= 1 || r >= 11 || c === 0 || c === MAP_COLS - 1 ? 1 : 0)
        )
      )
      return {
        cols: MAP_COLS,
        rows: MAP_ROWS,
        tileSize: TILE_SIZE,
        collision,
        workstations: { ...WORKSTATION_TILES },
      }
    }

    function initNPCs(tilemapData: TilemapData) {
      for (const agent of agentsRef.current) {
        if (!npcStatesRef.current.has(agent.id)) {
          const startTile = randomWalkableTile(tilemapData)
          npcStatesRef.current.set(agent.id, initNPCState(agent.id, startTile))
        }
      }
    }

    function renderTileCanvas(tilemapData: TilemapData) {
      const ctx = tileCanvas.getContext("2d")!
      ctx.clearRect(0, 0, tileCanvas.width, tileCanvas.height)

      if (DEBUG_GRID) {
        ctx.strokeStyle = "rgba(255,255,255,0.15)"
        ctx.lineWidth = 0.5
        for (let c = 0; c <= MAP_COLS; c++) {
          ctx.beginPath()
          ctx.moveTo(c * TILE_SIZE, 0)
          ctx.lineTo(c * TILE_SIZE, MAP_ROWS * TILE_SIZE)
          ctx.stroke()
        }
        for (let r = 0; r <= MAP_ROWS; r++) {
          ctx.beginPath()
          ctx.moveTo(0, r * TILE_SIZE)
          ctx.lineTo(MAP_COLS * TILE_SIZE, r * TILE_SIZE)
          ctx.stroke()
        }
      }

      // Marker workstation colorati con accent dell'agente (20% opacità)
      for (const [agentId, tile] of Object.entries(tilemapData.workstations)) {
        const agent = agentsRef.current.find((a) => a.id === agentId)
        if (!agent) continue
        const x = tile.col * TILE_SIZE
        const y = tile.row * TILE_SIZE
        ctx.fillStyle = agent.accent + "33"
        ctx.fillRect(x, y, TILE_SIZE, TILE_SIZE)
        ctx.strokeStyle = agent.accent
        ctx.lineWidth = 1
        ctx.strokeRect(x + 0.5, y + 0.5, TILE_SIZE - 1, TILE_SIZE - 1)
      }
    }

    function renderNPCCanvas(ts: number) {
      const ctx = npcCanvas.getContext("2d")!
      ctx.clearRect(0, 0, npcCanvas.width, npcCanvas.height)

      for (const [agentId, npc] of npcStatesRef.current) {
        const agent = agentsRef.current.find((a) => a.id === agentId)
        if (!agent) continue

        const { x, y } = npc

        // 1. Glow sotto lo sprite quando attivo
        if (npc.mode === "going_to_workstation" || npc.mode === "at_workstation") {
          ctx.save()
          ctx.globalAlpha = 0.3
          ctx.fillStyle = agent.accent
          ctx.beginPath()
          ctx.ellipse(x, y + 6, 8, 4, 0, 0, Math.PI * 2)
          ctx.fill()
          ctx.restore()
        }

        // 2. Sprite body: 10×14 centrato su (x, y)
        const bx = Math.round(x - 5)
        const by = Math.round(y - 7)

        // Body bianco crema
        ctx.fillStyle = "#F5F5F0"
        ctx.fillRect(bx, by + 4, 10, 10)
        // Header colorato
        ctx.fillStyle = agent.accent
        ctx.fillRect(bx, by, 10, 4)
        // Bordo
        ctx.strokeStyle = "#333"
        ctx.lineWidth = 1
        ctx.strokeRect(bx + 0.5, by + 0.5, 9, 13)

        // 3. Icon badge sopra la testa
        ctx.save()
        ctx.fillStyle = agent.accent
        ctx.font = "5px 'Press Start 2P', monospace"
        ctx.textAlign = "center"
        ctx.textBaseline = "bottom"
        ctx.fillText(agent.icon, x, by - 2)
        ctx.restore()

        // 4. Indicatore di stato
        ctx.save()
        ctx.font = "5px 'Press Start 2P', monospace"
        ctx.textAlign = "center"
        ctx.textBaseline = "bottom"

        if (npc.mode === "going_to_workstation") {
          const visible = Math.sin(ts / 300) > 0
          if (visible) {
            ctx.fillStyle = "#FFFFFF"
            ctx.fillText(">>>", x, by - 8)
          }
        } else if (npc.mode === "at_workstation") {
          ctx.fillStyle = agent.accent
          ctx.fillText("\u2605", x, by - 8) // ★
        } else if (npc.mode === "returning") {
          ctx.fillStyle = "#FFFFFF"
          ctx.fillText("\u2190", x, by - 8) // ←
        }

        ctx.restore()
      }
    }

    // Game loop
    function loop(ts: number) {
      const dt = Math.min((ts - lastTimestampRef.current) / 1000, 0.05)
      lastTimestampRef.current = ts

      const map = tilemapRef.current
      if (!map) {
        rafRef.current = requestAnimationFrame(loop)
        return
      }

      for (const [agentId, npc] of npcStatesRef.current) {
        const agent = agentsRef.current.find((a) => a.id === agentId)
        if (!agent) continue
        const workstationTile = map.workstations[agentId] ?? npc.homeTile
        let updated = transitionNPCMode(npc, agent.status, workstationTile, map)
        updated = updateNPC(updated, dt, map)
        npcStatesRef.current.set(agentId, updated)
      }

      renderNPCCanvas(ts)
      rafRef.current = requestAnimationFrame(loop)
    }

    // Click handler con hit-test manuale
    const handleClick = (e: MouseEvent) => {
      const rect = npcCanvas.getBoundingClientRect()
      const scaleX = (MAP_COLS * TILE_SIZE) / rect.width
      const scaleY = (MAP_ROWS * TILE_SIZE) / rect.height
      const wx = (e.clientX - rect.left) * scaleX
      const wy = (e.clientY - rect.top) * scaleY
      for (const [agentId, npc] of npcStatesRef.current) {
        if (Math.abs(wx - npc.x) < 8 && Math.abs(wy - npc.y) < 10) {
          const agent = agentsRef.current.find((a) => a.id === agentId)
          if (agent) {
            onAgentClickRef.current(agent)
            break
          }
        }
      }
    }

    npcCanvas.addEventListener("click", handleClick)

    // Carica tilemap JSON, con fallback
    fetch("/tilemap.json")
      .then((r) => r.json())
      .then((json: TiledJSON) => {
        if (!mounted) return
        const tilemapData = parseTiledJSON(json)
        tilemapRef.current = tilemapData
        initNPCs(tilemapData)
        renderTileCanvas(tilemapData)
        lastTimestampRef.current = performance.now()
        rafRef.current = requestAnimationFrame(loop)
      })
      .catch(() => {
        if (!mounted) return
        const fallbackMap = buildFallbackMap()
        tilemapRef.current = fallbackMap
        initNPCs(fallbackMap)
        renderTileCanvas(fallbackMap)
        lastTimestampRef.current = performance.now()
        rafRef.current = requestAnimationFrame(loop)
      })

    return () => {
      mounted = false
      cancelAnimationFrame(rafRef.current)
      npcCanvas.removeEventListener("click", handleClick)
    }
  }, []) // solo al mount

  const blackboardStyle: React.CSSProperties = {
    left: `${BLACKBOARD_POS.x}%`,
    top: `${BLACKBOARD_POS.y}%`,
    transform: "translate(-50%, -50%)",
    width: 200,
    height: 120,
  }

  return (
    <div
      ref={mapRef}
      style={{ position: "relative", width: "100vw", height: "100vh", overflow: "hidden" }}
    >
      {/* Layer 0 — sfondo pittorico */}
      <img
        src="/lab-bg.png"
        alt="Lab"
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          objectFit: "contain",
          objectPosition: "center",
          imageRendering: "pixelated",
          zIndex: 0,
          display: "block",
        }}
      />

      {/* Layer 1 — tile overlay (workstation markers, debug grid) */}
      <canvas
        ref={tileCanvasRef}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          zIndex: 1,
          pointerEvents: "none",
        }}
      />

      {/* Layer 2 — NPC canvas */}
      <canvas
        ref={npcCanvasRef}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          zIndex: 2,
          cursor: "pointer",
        }}
      />

      {/* Layer 3 — blackboard hotspot (React DOM) */}
      <div
        className="absolute cursor-pointer"
        style={{ ...blackboardStyle, zIndex: 3 }}
        onClick={onBlackboardClick}
      >
        <div
          style={{
            width: "100%",
            height: "100%",
            backgroundColor: "rgba(45,90,61,0.75)",
            border: "6px solid #8B7355",
            borderRadius: 3,
            boxShadow: "inset 0 0 20px rgba(0,0,0,0.3), 0 4px 0 #5C4033",
          }}
        >
          <div
            className="p-2"
            style={{ fontFamily: "'Press Start 2P', monospace", color: "#C8E6C9" }}
          >
            <div style={{ fontSize: 6, marginBottom: 4 }}>{deal.leadName}</div>
            <div style={{ fontSize: 5, opacity: 0.8, marginBottom: 6 }}>{deal.serviceType}</div>
            <div style={{ fontSize: 5, marginBottom: 4 }}>STATUS: {deal.status}</div>
            {/* Barra progresso */}
            <div
              style={{
                width: "100%",
                height: 8,
                backgroundColor: "rgba(0,0,0,0.3)",
                borderRadius: 1,
              }}
            >
              <div
                style={{
                  width: `${deal.progress}%`,
                  height: "100%",
                  backgroundColor: "#A5D6A7",
                  borderRadius: 1,
                  transition: "width 0.5s ease",
                }}
              />
            </div>
            <div className="flex justify-between mt-1" style={{ fontSize: 4 }}>
              <span>{deal.progress}%</span>
              <div className="flex" style={{ gap: 4 }}>
                <span style={{ color: deal.gates.proposal_approved ? "#66BB6A" : "#EF5350" }}>
                  G1
                </span>
                <span style={{ color: deal.gates.kickoff_confirmed ? "#66BB6A" : "#EF5350" }}>
                  G2
                </span>
                <span style={{ color: deal.gates.delivery_approved ? "#66BB6A" : "#EF5350" }}>
                  G3
                </span>
              </div>
            </div>
          </div>
          {/* Chalk tray */}
          <div
            className="absolute -bottom-3 left-2 right-2"
            style={{ height: 4, backgroundColor: "#8B7355", borderRadius: 1 }}
          />
        </div>
      </div>
    </div>
  )
}
