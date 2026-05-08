/**
 * Pure helper functions for NicheTable — Gap column (C.3).
 */

export interface GapBadgeStyle {
  background: string
  color: string
}

/** Returns badge style based on gap description length.
 *  Long gaps (>20 chars) → green; short/medium → yellow. */
export function gapBadgeStyle(gap: string): GapBadgeStyle {
  if (gap.length > 20) {
    return { background: '#d1fae5', color: '#065f46' }
  }
  return { background: '#fef9c3', color: '#713f12' }
}

/** Truncates gap label to 25 chars, appending '…' if cut. */
export function gapLabel(gap: string | null): string | null {
  if (!gap) return null
  if (gap.length <= 25) return gap
  return gap.slice(0, 25) + '…'
}
