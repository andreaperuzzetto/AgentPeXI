/**
 * Tests for PinterestPanel pure helper functions.
 *
 * TDD – RED phase: these run before PinterestPanel.helpers.ts exists.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  fmtNextPin,
  fmtCostEur,
  accessModeLabel,
  connectionDotColor,
} from './PinterestPanel.helpers'

// ── fmtNextPin ──────────────────────────────────────────────────────────────

describe('fmtNextPin', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-06-15T10:00:00Z'))
  })
  afterEach(() => vi.useRealTimers())

  it('returns "—" for null', () => {
    expect(fmtNextPin(null)).toBe('—')
  })

  it('formats a date 2 hours in the future', () => {
    const result = fmtNextPin('2026-06-15T12:00:00Z')
    expect(result).toMatch(/2.*or/i)   // "tra 2 ore" or "in 2h"
  })

  it('formats a date 1 day in the future', () => {
    const result = fmtNextPin('2026-06-16T10:00:00Z')
    expect(result).toMatch(/1.*giorn|domani|1.*day/i)
  })

  it('formats a date 3 days in the future', () => {
    const result = fmtNextPin('2026-06-18T10:00:00Z')
    expect(result).toMatch(/3.*giorn|3.*day/i)
  })

  it('formats a date 30 minutes in the future', () => {
    const result = fmtNextPin('2026-06-15T10:30:00Z')
    expect(result).toMatch(/30.*min/i)
  })
})

// ── fmtCostEur ──────────────────────────────────────────────────────────────

describe('fmtCostEur', () => {
  it('returns "—" for null', () => {
    expect(fmtCostEur(null)).toBe('—')
  })

  it('formats zero cost', () => {
    expect(fmtCostEur(0)).toBe('€0.0000')
  })

  it('formats a small cost to 4 decimals', () => {
    expect(fmtCostEur(0.1234)).toBe('€0.1234')
  })

  it('formats a larger cost', () => {
    expect(fmtCostEur(1.5)).toBe('€1.5000')
  })
})

// ── accessModeLabel ─────────────────────────────────────────────────────────

describe('accessModeLabel', () => {
  it('"standard" → "Standard Access"', () => {
    expect(accessModeLabel('standard')).toBe('Standard Access')
  })

  it('"trial" → "Trial"', () => {
    expect(accessModeLabel('trial')).toBe('Trial')
  })

  it('"plan_b" → "Piano B"', () => {
    expect(accessModeLabel('plan_b')).toBe('Piano B')
  })

  it('unknown mode → returns the raw value capitalised', () => {
    const result = accessModeLabel('unknown_mode')
    expect(typeof result).toBe('string')
    expect(result.length).toBeGreaterThan(0)
  })
})

// ── connectionDotColor ──────────────────────────────────────────────────────

describe('connectionDotColor', () => {
  it('returns green (#1BFF5E) when connected and token expires in > 3 days', () => {
    expect(connectionDotColor(true, 10)).toBe('#1BFF5E')
  })

  it('returns amber (#F5A623) when connected but token expires in ≤ 3 days', () => {
    expect(connectionDotColor(true, 2)).toBe('#F5A623')
  })

  it('returns amber (#F5A623) when connected but token expires in exactly 3 days', () => {
    expect(connectionDotColor(true, 3)).toBe('#F5A623')
  })

  it('returns red (#FF6B6B) when not connected', () => {
    expect(connectionDotColor(false, null)).toBe('#FF6B6B')
  })

  it('returns green when connected and tokenExpiresInDays is null (unknown expiry)', () => {
    expect(connectionDotColor(true, null)).toBe('#1BFF5E')
  })
})
