/**
 * BudgetGauges — pure helpers and shared config (testable without DOM).
 */

export interface GaugeConfig {
  key:   string
  label: string
  color: string
  flex:  number   // layout weight
  size:  number   // SVG size px
  delay: number   // mount stagger
}

export const GAUGES: GaugeConfig[] = [
  { key: 'llm',       label: 'LLM',       color: '#F5A623', flex: 1.4, size: 128, delay: 0.00 },
  { key: 'image',     label: 'Image',     color: '#B57BFF', flex: 1.0, size: 110, delay: 0.08 },
  { key: 'fee',       label: 'Fee',       color: '#C8C8FF', flex: 0.8, size: 98,  delay: 0.16 },
  { key: 'pinterest', label: 'Pinterest', color: '#00CED1', flex: 0.8, size: 98,  delay: 0.24 },
]

export function usdStr(v: number): string {
  if (v <= 0) return '$0'
  if (v < 0.01) return '<$0.01'
  return `$${v.toFixed(2)}`
}

export function pctOf(value: number, limit: number): number {
  if (limit <= 0) return 0
  return Math.round((value / limit) * 100)
}
