/**
 * Tests for ProductionPipeline.helpers — C.4 cross-ref helpers.
 *
 * TDD – RED phase: runs before ProductionPipeline.helpers.ts exists.
 */
import { describe, it, expect } from 'vitest'
import { hasActiveCrossref, crossrefLabel, clusterProgressLabel } from './ProductionPipeline.helpers'

/* Minimal item shape used by helpers */
interface ItemStub {
  etsy_listing_id: string | null
  cluster_id: string | null
}

/* Minimal cluster stat shape returned by /api/etsy/clusters */
interface ClusterStat {
  cluster_id: string
  total: number
  completed: number
}

// ── hasActiveCrossref ─────────────────────────────────────────────────────────

describe('hasActiveCrossref', () => {
  const clusters: ClusterStat[] = [
    { cluster_id: 'abc123', total: 6, completed: 3 },
    { cluster_id: 'xyz456', total: 6, completed: 1 },
  ]

  it('returns false when etsy_listing_id is null', () => {
    const item: ItemStub = { etsy_listing_id: null, cluster_id: 'abc123' }
    expect(hasActiveCrossref(item, clusters)).toBe(false)
  })

  it('returns false when cluster_id is null', () => {
    const item: ItemStub = { etsy_listing_id: '123456789', cluster_id: null }
    expect(hasActiveCrossref(item, clusters)).toBe(false)
  })

  it('returns false when no matching cluster found', () => {
    const item: ItemStub = { etsy_listing_id: '123456789', cluster_id: 'unknown' }
    expect(hasActiveCrossref(item, clusters)).toBe(false)
  })

  it('returns false when matching cluster has fewer than 2 completed items', () => {
    const item: ItemStub = { etsy_listing_id: '123456789', cluster_id: 'xyz456' }
    expect(hasActiveCrossref(item, clusters)).toBe(false)
  })

  it('returns false when matching cluster has exactly 1 completed item', () => {
    const oneCompleted: ClusterStat[] = [
      { cluster_id: 'solo', total: 6, completed: 1 },
    ]
    const item: ItemStub = { etsy_listing_id: '111', cluster_id: 'solo' }
    expect(hasActiveCrossref(item, oneCompleted)).toBe(false)
  })

  it('returns true when etsy_listing_id present and cluster has 2+ completed', () => {
    const item: ItemStub = { etsy_listing_id: '987654321', cluster_id: 'abc123' }
    expect(hasActiveCrossref(item, clusters)).toBe(true)
  })

  it('returns true at the boundary of exactly 2 completed', () => {
    const twoCompleted: ClusterStat[] = [
      { cluster_id: 'edge', total: 6, completed: 2 },
    ]
    const item: ItemStub = { etsy_listing_id: '111', cluster_id: 'edge' }
    expect(hasActiveCrossref(item, twoCompleted)).toBe(true)
  })
})

// ── crossrefLabel ─────────────────────────────────────────────────────────────

describe('crossrefLabel', () => {
  it('returns singular form for count 1', () => {
    expect(crossrefLabel(1)).toBe('1 listing collegato')
  })

  it('returns plural form for count 3', () => {
    expect(crossrefLabel(3)).toBe('3 listing collegati')
  })

  it('returns plural form for count 2', () => {
    expect(crossrefLabel(2)).toBe('2 listing collegati')
  })

  it('returns plural form for count 0', () => {
    expect(crossrefLabel(0)).toBe('0 listing collegati')
  })
})

// ── clusterProgressLabel ──────────────────────────────────────────────────────

describe('clusterProgressLabel', () => {
  it('returns N/M format', () => {
    expect(clusterProgressLabel(3, 6)).toBe('3/6')
  })

  it('returns 0/6 when no items completed', () => {
    expect(clusterProgressLabel(0, 6)).toBe('0/6')
  })

  it('returns 6/6 when all completed', () => {
    expect(clusterProgressLabel(6, 6)).toBe('6/6')
  })
})
