import { describe, it, expect } from 'vitest'
import { springEntry, SIDEBAR_PANELS } from './EtsyView.helpers'

describe('springEntry', () => {
  it('returns opacity 0 and y 14 as initial state', () => {
    const result = springEntry(0)
    expect(result.initial).toEqual({ opacity: 0, y: 14 })
  })

  it('returns opacity 1 and y 0 as animate state', () => {
    const result = springEntry(0)
    expect(result.animate).toEqual({ opacity: 1, y: 0 })
  })

  it('passes delay through to transition', () => {
    const result = springEntry(0.12)
    expect(result.transition.delay).toBe(0.12)
  })

  it('uses spring type in transition', () => {
    const result = springEntry(0)
    expect(result.transition.type).toBe('spring')
  })

  it('zero delay produces delay 0 in transition', () => {
    const result = springEntry(0)
    expect(result.transition.delay).toBe(0)
  })
})

describe('SIDEBAR_PANELS', () => {
  it('includes PinterestPanel entry', () => {
    const names = SIDEBAR_PANELS.map(p => p.name)
    expect(names).toContain('PinterestPanel')
  })

  it('PinterestPanel comes after AdsStatus', () => {
    const names = SIDEBAR_PANELS.map(p => p.name)
    const adsIdx = names.indexOf('AdsStatus')
    const pinIdx = names.indexOf('PinterestPanel')
    expect(pinIdx).toBeGreaterThan(adsIdx)
  })

  it('PinterestPanel delay is 0.12', () => {
    const panel = SIDEBAR_PANELS.find(p => p.name === 'PinterestPanel')
    expect(panel?.delay).toBe(0.12)
  })

  it('has ShopOptimizerCard as last panel', () => {
    const last = SIDEBAR_PANELS[SIDEBAR_PANELS.length - 1]
    expect(last.name).toBe('ShopOptimizerCard')
  })
})
