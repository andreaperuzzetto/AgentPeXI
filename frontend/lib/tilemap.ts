export const TILE_SIZE = 16 // px — corrisponde ai pixel nativi di lab-bg.png
export const MAP_COLS = 15 // 240 / 16
export const MAP_ROWS = 14 // 224 / 16

export type TileCell = 0 | 1 | 2 // 0 = caminabile, 1 = impassabile, 2 = workstation slot

export interface GridPos {
  col: number
  row: number
}

export interface TilemapData {
  cols: number
  rows: number
  tileSize: number
  collision: TileCell[][]
  workstations: Record<string, GridPos>
}

// Interfacce Tiled minimali
interface TiledTileLayer {
  type: "tilelayer"
  name: string
  data: number[]
  width: number
  height: number
}

interface TiledObject {
  name: string
  x: number
  y: number
  width: number
  height: number
}

interface TiledObjectLayer {
  type: "objectgroup"
  name: string
  objects: TiledObject[]
}

export interface TiledJSON {
  width: number
  height: number
  tilewidth: number
  tileheight: number
  layers: (TiledTileLayer | TiledObjectLayer | { type: string; name: string })[]
}

export function parseTiledJSON(json: TiledJSON): TilemapData {
  const cols = json.width
  const rows = json.height

  const collision: TileCell[][] = Array.from({ length: rows }, () =>
    new Array<TileCell>(cols).fill(0)
  )

  const workstations: Record<string, GridPos> = {}

  for (const layer of json.layers) {
    if (layer.type === "tilelayer" && layer.name === "collision") {
      const tileLayer = layer as TiledTileLayer
      for (let i = 0; i < tileLayer.data.length; i++) {
        const row = Math.floor(i / cols)
        const col = i % cols
        if (row < rows && col < cols) {
          collision[row][col] = tileLayer.data[i] > 0 ? 1 : 0
        }
      }
    } else if (layer.type === "objectgroup" && layer.name === "workstations") {
      const objectLayer = layer as TiledObjectLayer
      for (const obj of objectLayer.objects) {
        workstations[obj.name] = {
          col: Math.floor(obj.x / json.tilewidth),
          row: Math.floor(obj.y / json.tileheight),
        }
      }
    }
  }

  return { cols, rows, tileSize: json.tilewidth, collision, workstations }
}

export function isTileWalkable(map: TilemapData, col: number, row: number): boolean {
  if (col < 0 || col >= map.cols || row < 0 || row >= map.rows) return false
  return map.collision[row][col] === 0
}

/** Restituisce le coordinate pixel del centro del tile */
export function tileToPixel(pos: GridPos): { x: number; y: number } {
  return {
    x: pos.col * TILE_SIZE + TILE_SIZE / 2,
    y: pos.row * TILE_SIZE + TILE_SIZE / 2,
  }
}

export function pixelToTile(x: number, y: number): GridPos {
  return {
    col: Math.floor(x / TILE_SIZE),
    row: Math.floor(y / TILE_SIZE),
  }
}
