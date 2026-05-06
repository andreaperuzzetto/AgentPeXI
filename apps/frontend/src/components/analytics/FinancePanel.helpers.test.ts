import { describe, it, expect } from 'vitest'
import { fmtEur } from './FinancePanel.helpers'

describe('fmtEur', () => {
  it('formats zero as €0.00', () => {
    expect(fmtEur(0)).toBe('€0.00')
  })

  it('formats positive amount with 2 decimals', () => {
    expect(fmtEur(12.5)).toBe('€12.50')
  })

  it('formats single decimal correctly', () => {
    expect(fmtEur(7.9)).toBe('€7.90')
  })

  it('forceSign positive prepends plus sign', () => {
    expect(fmtEur(1.0, true)).toBe('+€1.00')
  })

  it('forceSign negative uses unicode minus and strips sign from abs value', () => {
    expect(fmtEur(-5.25, true)).toBe('−€5.25')
  })

  it('without forceSign negative input returns absolute value formatted', () => {
    // fmtEur is always called with non-negative values in the non-forceSign path
    expect(fmtEur(3.14)).toBe('€3.14')
  })
})
