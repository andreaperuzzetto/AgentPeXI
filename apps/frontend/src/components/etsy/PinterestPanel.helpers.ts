/**
 * PinterestPanel — pure helper functions (testable without DOM).
 */

/** Format an ISO date string as a human-readable relative time (Italian). */
export function fmtNextPin(isoDate: string | null): string {
  if (!isoDate) return '—'
  const now   = Date.now()
  const diff  = new Date(isoDate).getTime() - now
  if (diff <= 0) return 'ora'

  const minutes = Math.round(diff / 60_000)
  if (minutes < 60)  return `tra ${minutes} min`
  const hours = Math.round(diff / 3_600_000)
  if (hours < 24) return `tra ${hours} or${hours === 1 ? 'a' : 'e'}`
  const days = Math.round(diff / 86_400_000)
  if (days === 1)  return 'domani'
  return `tra ${days} giorni`
}

/** Format a euro cost to 4 decimal places, or "—" for null. */
export function fmtCostEur(cost: number | null): string {
  if (cost === null) return '—'
  return `€${cost.toFixed(4)}`
}

/** Map access_mode string to a human-readable label. */
export function accessModeLabel(mode: string): string {
  switch (mode) {
    case 'standard': return 'Standard Access'
    case 'trial':    return 'Trial'
    case 'plan_b':   return 'Piano B'
    default:         return mode.charAt(0).toUpperCase() + mode.slice(1).replace(/_/g, ' ')
  }
}

/**
 * Dot color based on connection state.
 * - Not connected → red
 * - Connected, expires ≤ 3 days → amber
 * - Connected, expires > 3 days (or unknown) → green
 */
export function connectionDotColor(
  connected: boolean,
  tokenExpiresInDays: number | null,
): string {
  if (!connected) return '#FF6B6B'
  if (tokenExpiresInDays !== null && tokenExpiresInDays <= 3) return '#F5A623'
  return '#1BFF5E'
}
