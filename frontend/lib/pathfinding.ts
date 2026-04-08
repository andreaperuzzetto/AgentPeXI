import { isTileWalkable } from "./tilemap"
import type { TilemapData, GridPos } from "./tilemap"

interface AStarNode {
  pos: GridPos
  g: number
  h: number
  f: number
  parent: AStarNode | null
}

const DIRS: GridPos[] = [
  { col: 0, row: -1 }, // up
  { col: 0, row: 1 },  // down
  { col: -1, row: 0 }, // left
  { col: 1, row: 0 },  // right
]

function heuristic(a: GridPos, b: GridPos): number {
  return Math.abs(a.col - b.col) + Math.abs(a.row - b.row)
}

function posKey(pos: GridPos): string {
  return `${pos.col},${pos.row}`
}

/**
 * Trova il percorso A* su griglia 2D (4 direzioni, no diagonale).
 * Ritorna l'array di tile dal successore di `from` fino a `to` incluso.
 * Ritorna [] se il path non esiste.
 * Se `to` è impassabile, usa il tile caminabile adiacente più vicino a `from`.
 */
export function findPath(map: TilemapData, from: GridPos, to: GridPos): GridPos[] {
  // Se from === to, niente da fare
  if (from.col === to.col && from.row === to.row) return []

  // Se to è impassabile, trovare il tile caminabile adiacente più vicino
  let target = to
  if (!isTileWalkable(map, to.col, to.row)) {
    let best: GridPos | null = null
    let bestDist = Infinity
    for (const dir of DIRS) {
      const neighbor: GridPos = { col: to.col + dir.col, row: to.row + dir.row }
      if (isTileWalkable(map, neighbor.col, neighbor.row)) {
        const dist = heuristic(from, neighbor)
        if (dist < bestDist) {
          bestDist = dist
          best = neighbor
        }
      }
    }
    if (!best) return []
    target = best
  }

  // Se from è lo stesso del target dopo la correzione
  if (from.col === target.col && from.row === target.row) return []

  const open: AStarNode[] = []
  const closed = new Set<string>()
  const gScore = new Map<string, number>()

  const startNode: AStarNode = {
    pos: from,
    g: 0,
    h: heuristic(from, target),
    f: heuristic(from, target),
    parent: null,
  }
  open.push(startNode)
  gScore.set(posKey(from), 0)

  while (open.length > 0) {
    // Trovare il nodo con f minore (array semplice, nessuna dipendenza esterna)
    let lowestIdx = 0
    for (let i = 1; i < open.length; i++) {
      if (open[i].f < open[lowestIdx].f) lowestIdx = i
    }
    const current = open.splice(lowestIdx, 1)[0]
    const currentKey = posKey(current.pos)

    if (closed.has(currentKey)) continue
    closed.add(currentKey)

    if (current.pos.col === target.col && current.pos.row === target.row) {
      // Ricostruire il path (escludendo `from`)
      const path: GridPos[] = []
      let node: AStarNode | null = current
      while (node) {
        path.unshift(node.pos)
        node = node.parent
      }
      return path.slice(1) // rimuovere il punto di partenza
    }

    for (const dir of DIRS) {
      const next: GridPos = {
        col: current.pos.col + dir.col,
        row: current.pos.row + dir.row,
      }
      if (!isTileWalkable(map, next.col, next.row)) continue

      const nextKey = posKey(next)
      if (closed.has(nextKey)) continue

      const tentativeG = current.g + 1
      if (tentativeG < (gScore.get(nextKey) ?? Infinity)) {
        gScore.set(nextKey, tentativeG)
        const h = heuristic(next, target)
        open.push({
          pos: next,
          g: tentativeG,
          h,
          f: tentativeG + h,
          parent: current,
        })
      }
    }
  }

  return [] // nessun path trovato
}
