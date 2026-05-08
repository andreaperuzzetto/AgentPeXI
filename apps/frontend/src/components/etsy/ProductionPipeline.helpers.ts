/**
 * Pure helper functions for ProductionPipeline — C.4 cross-ref.
 */

export interface ClusterStat {
  cluster_id: string
  total: number
  completed: number
}

interface CrossrefItem {
  etsy_listing_id: string | null
  cluster_id: string | null
}

/** Returns true when an item has an active cross-reference:
 *  etsy_listing_id must be present AND the item's cluster must have ≥2 published listings. */
export function hasActiveCrossref(item: CrossrefItem, clusters: ClusterStat[]): boolean {
  if (!item.etsy_listing_id || !item.cluster_id) return false
  const cluster = clusters.find(c => c.cluster_id === item.cluster_id)
  if (!cluster) return false
  return cluster.completed >= 2
}

/** Returns a human-readable label for the number of cross-linked listings. */
export function crossrefLabel(count: number): string {
  return count === 1 ? '1 listing collegato' : `${count} listing collegati`
}

/** Returns "published/total" progress label for a cluster. */
export function clusterProgressLabel(published: number, total: number): string {
  return `${published}/${total}`
}
