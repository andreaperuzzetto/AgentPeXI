/**
 * Tests for NicheTable.helpers — Gap column helpers (C.3).
 *
 * TDD – RED phase: runs before NicheTable.helpers.ts exists.
 */
import { describe, it, expect } from 'vitest'
import { gapBadgeStyle, gapLabel } from './NicheTable.helpers'

// ── gapBadgeStyle ────────────────────────────────────────────────────────────

describe('gapBadgeStyle', () => {
  it('returns green background when gap is longer than 20 chars', () => {
    const style = gapBadgeStyle('No ADHD bundle under $5 — big gap here')
    expect(style.background).toBe('#d1fae5')
    expect(style.color).toBe('#065f46')
  })

  it('returns yellow background when gap is 20 chars or fewer', () => {
    const style = gapBadgeStyle('Short gap text')
    expect(style.background).toBe('#fef9c3')
    expect(style.color).toBe('#713f12')
  })

  it('threshold: exactly 21 chars → green', () => {
    const gap = 'a'.repeat(21)
    const style = gapBadgeStyle(gap)
    expect(style.background).toBe('#d1fae5')
  })

  it('threshold: exactly 20 chars → yellow', () => {
    const gap = 'a'.repeat(20)
    const style = gapBadgeStyle(gap)
    expect(style.background).toBe('#fef9c3')
  })
})

// ── gapLabel ─────────────────────────────────────────────────────────────────

describe('gapLabel', () => {
  it('returns the full string when ≤ 25 chars', () => {
    expect(gapLabel('Short')).toBe('Short')
  })

  it('truncates at 25 chars and appends ellipsis when longer', () => {
    const gap = 'No ADHD bundle under $5 — big gap here'
    expect(gapLabel(gap)).toBe('No ADHD bundle under $5 —…')
  })

  it('returns exactly 26 chars when input is 26+ chars (25 + …)', () => {
    const gap = 'a'.repeat(30)
    const result = gapLabel(gap)
    expect(result).toBe('a'.repeat(25) + '…')
  })

  it('exactly 25 chars → no ellipsis added', () => {
    const gap = 'a'.repeat(25)
    expect(gapLabel(gap)).toBe(gap)
  })
})
