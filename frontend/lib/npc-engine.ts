import { isTileWalkable, tileToPixel, pixelToTile } from "./tilemap"
import type { TilemapData, GridPos } from "./tilemap"
import { findPath } from "./pathfinding"
import type { AgentStatus } from "./lab-data"

export type NPCMode = "wandering" | "going_to_workstation" | "at_workstation" | "returning"

export interface NPCState {
  agentId: string
  x: number // posizione corrente in px (world space)
  y: number
  path: GridPos[] // path calcolato da A*
  pathIndex: number
  mode: NPCMode
  homeTile: GridPos // tile corrente di "riposo"
  speed: number // px/sec — default 48 (= 3 tile/sec)
  facing: "up" | "down" | "left" | "right"
  wanderCooldown: number // secondi da aspettare prima del prossimo wander
  lastAgentStatus: string
}

export function initNPCState(agentId: string, startTile: GridPos): NPCState {
  const { x, y } = tileToPixel(startTile)
  return {
    agentId,
    x,
    y,
    path: [],
    pathIndex: 0,
    mode: "wandering",
    homeTile: startTile,
    speed: 48,
    facing: "down",
    wanderCooldown: Math.random() * 2, // piccolo offset iniziale per desincronizzare gli NPC
    lastAgentStatus: "idle",
  }
}

/**
 * Aggiorna la modalità NPC in base allo stato dell'agente.
 * Chiamare prima di updateNPC().
 */
export function transitionNPCMode(
  npc: NPCState,
  agentStatus: AgentStatus,
  workstationTile: GridPos,
  map: TilemapData
): NPCState {
  const updated: NPCState = { ...npc, lastAgentStatus: agentStatus }

  // WANDERING → GOING_TO_WORKSTATION
  if (agentStatus === "running" && npc.mode === "wandering") {
    const from = pixelToTile(npc.x, npc.y)
    const path = findPath(map, from, workstationTile)
    return { ...updated, mode: "going_to_workstation", path, pathIndex: 0 }
  }

  // AT_WORKSTATION → RETURNING
  if (agentStatus !== "running" && npc.mode === "at_workstation") {
    const from = pixelToTile(npc.x, npc.y)
    const path = findPath(map, from, npc.homeTile)
    return { ...updated, mode: "returning", path, pathIndex: 0 }
  }

  return updated
}

/** Trova un tile caminabile casuale entro un raggio di 4–7 tile */
function findWanderTarget(map: TilemapData, currentTile: GridPos): GridPos | null {
  for (let attempt = 0; attempt < 30; attempt++) {
    const radius = 4 + Math.random() * 3 // 4–7
    const angle = Math.random() * Math.PI * 2
    const col = Math.round(currentTile.col + Math.cos(angle) * radius)
    const row = Math.round(currentTile.row + Math.sin(angle) * radius)
    if (isTileWalkable(map, col, row)) {
      return { col, row }
    }
  }
  return null
}

/**
 * Avanza il movimento di dt secondi lungo il path corrente.
 * Gestisce la logica di wander quando il path è vuoto.
 */
export function updateNPC(npc: NPCState, dt: number, map: TilemapData): NPCState {
  let updated: NPCState = { ...npc }

  // Tick wander cooldown
  if (updated.wanderCooldown > 0) {
    updated.wanderCooldown = Math.max(0, updated.wanderCooldown - dt)
  }

  // Wandering: scegliere nuovo path se idle
  if (updated.mode === "wandering" && updated.path.length === 0 && updated.wanderCooldown <= 0) {
    const currentTile = pixelToTile(updated.x, updated.y)
    const target = findWanderTarget(map, currentTile)
    if (target !== null) {
      const path = findPath(map, currentTile, target)
      if (path.length > 0) {
        updated.path = path
        updated.pathIndex = 0
      } else {
        updated.wanderCooldown = 2 // tile irraggiungibile, ritentare dopo 2s
      }
    } else {
      updated.wanderCooldown = 2
    }
  }

  // Nessun path da seguire
  if (updated.path.length === 0 || updated.pathIndex >= updated.path.length) {
    return updated
  }

  // Muoversi lungo il path
  const targetTile = updated.path[updated.pathIndex]
  const targetPx = tileToPixel(targetTile)
  const dx = targetPx.x - updated.x
  const dy = targetPx.y - updated.y
  const dist = Math.sqrt(dx * dx + dy * dy)

  // Aggiornare facing in base alla direzione di movimento
  if (dist > 0.5) {
    if (Math.abs(dx) >= Math.abs(dy)) {
      updated.facing = dx > 0 ? "right" : "left"
    } else {
      updated.facing = dy > 0 ? "down" : "up"
    }
  }

  const step = updated.speed * dt

  if (dist <= 1 || step >= dist) {
    // Raggiunto il waypoint corrente
    updated.x = targetPx.x
    updated.y = targetPx.y
    updated.pathIndex++

    if (updated.pathIndex >= updated.path.length) {
      updated = handlePathComplete(updated)
    }
  } else {
    // Avanzare verso il tile target
    updated.x += (dx / dist) * step
    updated.y += (dy / dist) * step
  }

  return updated
}

function handlePathComplete(npc: NPCState): NPCState {
  if (npc.mode === "going_to_workstation") {
    return { ...npc, mode: "at_workstation", path: [], pathIndex: 0 }
  }

  if (npc.mode === "returning") {
    const arrivedTile = pixelToTile(npc.x, npc.y)
    return {
      ...npc,
      mode: "wandering",
      path: [],
      pathIndex: 0,
      homeTile: arrivedTile,
      wanderCooldown: 1.5 + Math.random() * 2.5, // 1.5–4s
    }
  }

  if (npc.mode === "wandering") {
    // Fine di un cammino di wander
    const arrivedTile = pixelToTile(npc.x, npc.y)
    return {
      ...npc,
      path: [],
      pathIndex: 0,
      homeTile: arrivedTile,
      wanderCooldown: 1.5 + Math.random() * 2.5, // 1.5–4s
    }
  }

  return npc
}
