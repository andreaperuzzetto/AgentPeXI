/**
 * FinancePanel — pure helpers (testable without DOM).
 */

export function fmtEur(n: number, forceSign = false): string {
  const abs = Math.abs(n).toFixed(2)
  if (forceSign) return n >= 0 ? `+€${abs}` : `−€${abs}`
  return `€${abs}`
}
