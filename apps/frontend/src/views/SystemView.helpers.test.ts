import { describe, it, expect } from 'vitest'
import {
  BUSINESS_AGENTS,
  SERVICE_AGENTS,
  SERVICE_STATUS_OVERRIDES,
} from './SystemView.helpers'

describe('BUSINESS_AGENTS', () => {
  it('includes a pinterest entry', () => {
    const names = BUSINESS_AGENTS.map((a) => a.name)
    expect(names).toContain('pinterest')
  })

  it('pinterest entry has layer business', () => {
    const entry = BUSINESS_AGENTS.find((a) => a.name === 'pinterest')
    expect(entry?.layer).toBe('business')
  })

  it('pinterest entry has pipelinePos "4 · pinterest"', () => {
    const entry = BUSINESS_AGENTS.find((a) => a.name === 'pinterest')
    expect(entry?.pipelinePos).toBe('4 · pinterest')
  })

  it('preserves existing research, design, publisher entries', () => {
    const names = BUSINESS_AGENTS.map((a) => a.name)
    expect(names).toContain('research')
    expect(names).toContain('design')
    expect(names).toContain('publisher')
  })
})

describe('SERVICE_AGENTS', () => {
  it('includes a pinterest_oauth entry', () => {
    const names = SERVICE_AGENTS.map((a) => a.name)
    expect(names).toContain('pinterest_oauth')
  })

  it('pinterest_oauth entry has layer service', () => {
    const entry = SERVICE_AGENTS.find((a) => a.name === 'pinterest_oauth')
    expect(entry?.layer).toBe('service')
  })

  it('pinterest_oauth entry has isService true', () => {
    const entry = SERVICE_AGENTS.find((a) => a.name === 'pinterest_oauth')
    expect(entry?.isService).toBe(true)
  })

  it('preserves existing autopilot_loop, learning_loop entries', () => {
    const names = SERVICE_AGENTS.map((a) => a.name)
    expect(names).toContain('autopilot_loop')
    expect(names).toContain('learning_loop')
  })
})

describe('SERVICE_STATUS_OVERRIDES', () => {
  it('contains pinterest_oauth', () => {
    expect(SERVICE_STATUS_OVERRIDES.has('pinterest_oauth')).toBe(true)
  })

  it('still contains existing overrides', () => {
    expect(SERVICE_STATUS_OVERRIDES.has('autopilot_loop')).toBe(true)
    expect(SERVICE_STATUS_OVERRIDES.has('finance_tracker')).toBe(true)
  })
})
