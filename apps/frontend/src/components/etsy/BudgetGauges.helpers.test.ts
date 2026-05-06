/**
 * Tests for BudgetGauges.helpers — 4th Pinterest gauge config.
 *
 * TDD – RED phase: runs before BudgetGauges.helpers.ts exists.
 */
import { describe, it, expect } from 'vitest'
import { GAUGES, pctOf, usdStr } from './BudgetGauges.helpers'

// ── GAUGES config ────────────────────────────────────────────────────────────

describe('GAUGES', () => {
  it('has exactly 4 entries (LLM, Image, Fee, Pinterest)', () => {
    expect(GAUGES).toHaveLength(4)
  })

  it('4th gauge has key "pinterest"', () => {
    expect(GAUGES[3].key).toBe('pinterest')
  })

  it('Pinterest gauge has teal color #00CED1', () => {
    expect(GAUGES[3].color).toBe('#00CED1')
  })

  it('Pinterest gauge has label "Pinterest"', () => {
    expect(GAUGES[3].label).toBe('Pinterest')
  })

  it('first 3 gauges are LLM, Image, Fee in order', () => {
    expect(GAUGES[0].key).toBe('llm')
    expect(GAUGES[1].key).toBe('image')
    expect(GAUGES[2].key).toBe('fee')
  })

  it('existing gauge colors are unchanged', () => {
    expect(GAUGES[0].color).toBe('#F5A623')  // LLM amber
    expect(GAUGES[1].color).toBe('#B57BFF')  // Image purple
    expect(GAUGES[2].color).toBe('#C8C8FF')  // Fee light purple
  })
})

// ── pctOf ────────────────────────────────────────────────────────────────────

describe('pctOf', () => {
  it('returns 0 when limit is 0', () => {
    expect(pctOf(10, 0)).toBe(0)
  })

  it('returns 50 for half of limit', () => {
    expect(pctOf(5, 10)).toBe(50)
  })

  it('returns 100 for full limit', () => {
    expect(pctOf(10, 10)).toBe(100)
  })

  it('can exceed 100 if value > limit', () => {
    expect(pctOf(15, 10)).toBe(150)
  })
})

// ── usdStr ────────────────────────────────────────────────────────────────────

describe('usdStr', () => {
  it('returns "$0" for zero', () => {
    expect(usdStr(0)).toBe('$0')
  })

  it('returns "<$0.01" for very small values', () => {
    expect(usdStr(0.001)).toBe('<$0.01')
  })

  it('formats a normal value to 2 decimal places', () => {
    expect(usdStr(1.5)).toBe('$1.50')
  })
})
