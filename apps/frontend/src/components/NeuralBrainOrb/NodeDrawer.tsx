/**
 * NodeDrawer — full-height detail panel for a ChromaDB memory node
 *
 * Ported and redesigned from NeuralBrain.tsx NodeDrawer (FE-2.5).
 * Triggered by raycaster click in NeuralBrainOrb → slides in from right.
 *
 * Layout:
 *   Full-height panel, 420px wide, positioned absolute right:0
 *   Header:    zone badge + node label + close button
 *   Body left (42%): access history scroll + connection chips
 *   Body right (58%): full document text + metadata table
 *
 * Data:
 *   Graph-level node data comes from the graph response (passed as props).
 *   Full detail (document, access history) fetched from GET /api/memory/node/{id}?collection=...
 *
 * Styling: Liquid Glass panel (backdrop-blur + border-left highlight),
 *   zone-coloured accents, Space Grotesk + JetBrains Mono, Double-Bezel header.
 */

import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'

/* ── Public types ───────────────────────────────────────────────────────── */

export interface GraphNode {
  id:           string
  label:        string
  collection:   string
  zone:         string
  /** First 300 chars of the source document — from /api/memory/graph */
  document?:    string
  /** Pre-computed connection count from backend */
  connections?: number
  metadata?:    Record<string, unknown>
}

export interface GraphEdge {
  source:  string
  target:  string
  weight?: number
}

interface NodeDrawerProps {
  nodeId:  string | null
  nodes:   GraphNode[]
  edges:   GraphEdge[]
  onClose: () => void
}

/* ── Private types ──────────────────────────────────────────────────────── */

interface NodeDetail {
  id:             string
  document:       string
  metadata:       Record<string, unknown>
  collection:     string
  access_history: Array<{
    agent:       string
    query_text:  string | null
    queried_at:  string
  }>
}

/* ── Zone colors (matches ZONE_COLOR_HEX in NeuralBrainOrb) ────────────── */

const ZONE_COLOR: Record<string, string> = {
  neural:    '#B57BFF',
  memory:    '#B57BFF',
  etsy:      '#F5A623',
  personal:  '#1BFF5E',
  shared:    '#C8C8FF',
  system:    '#8B8D98',
  analytics: '#C8C8FF',
}

function zoneColor(zone: string): string {
  return ZONE_COLOR[zone] ?? '#8B8D98'
}

function rgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${Math.min(1, Math.max(0, a)).toFixed(3)})`
}

/* ── Sub-components ─────────────────────────────────────────────────────── */

function LoadingRow() {
  return (
    <div style={{
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 11,
      color: '#3a3d47',
      letterSpacing: '0.12em',
      padding: '14px 0',
      textAlign: 'center',
    }}>
      · · ·
    </div>
  )
}

function EmptyRow({ text }: { text: string }) {
  return (
    <div style={{
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 11,
      color: '#3a3d47',
      letterSpacing: '0.08em',
      padding: '10px 0',
      fontStyle: 'italic',
    }}>
      {text}
    </div>
  )
}

/* ── Main component ─────────────────────────────────────────────────────── */

export function NodeDrawer({ nodeId, nodes, edges, onClose }: NodeDrawerProps) {
  const node   = nodeId ? (nodes.find(n => n.id === nodeId) ?? null) : null
  const col    = node ? zoneColor(node.zone) : '#8B8D98'

  const [detail, setDetail] = useState<NodeDetail | null>(null)
  const [loading, setLoading] = useState(false)

  /* Fetch full detail when node changes */
  useEffect(() => {
    if (!node) { setDetail(null); return }
    setDetail(null)
    setLoading(true)
    fetch(`/api/memory/node/${encodeURIComponent(node.id)}?collection=${node.collection}`)
      .then(r => r.ok ? r.json() : null)
      .then((d: NodeDetail | null) => { setDetail(d); setLoading(false) })
      .catch(() => { setLoading(false) })
  }, [node?.id, node?.collection]) // eslint-disable-line react-hooks/exhaustive-deps

  /* Close on Escape */
  useEffect(() => {
    if (!nodeId) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [nodeId, onClose])

  /* Connected nodes (from local edge data — no API call) */
  const connectedNodes: GraphNode[] = node
    ? edges
        .filter(e => e.source === node.id || e.target === node.id)
        .map(e => nodes.find(n => n.id === (e.source === node.id ? e.target : e.source)))
        .filter((n): n is GraphNode => n !== undefined)
        .slice(0, 8)
    : []

  return (
    <AnimatePresence>
      {node && (
        <motion.div
          key={node.id}
          initial={{ x: 40, opacity: 0 }}
          animate={{ x: 0,  opacity: 1 }}
          exit={{   x: 40,  opacity: 0 }}
          transition={{ type: 'spring', stiffness: 300, damping: 30 }}
          style={{
            position: 'absolute',
            top:    0,
            right:  0,
            height: '100%',
            width:  420,
            zIndex: 20,
            display: 'flex',
            flexDirection: 'column',
            /* Liquid Glass panel */
            background:    'var(--bg-s1, #0D0F12)',
            backdropFilter: 'blur(22px)',
            WebkitBackdropFilter: 'blur(22px)',
            borderLeft: `1px solid ${rgba(col, 0.18)}`,
            boxShadow: `-8px 0 48px rgba(0,0,0,0.55), -1px 0 0 ${rgba(col, 0.06)}`,
          }}
        >

          {/* ── Header — Double-Bezel treatment ── */}
          <div style={{
            flexShrink: 0,
            padding: '0 14px',
            height: 48,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            background: `${rgba(col, 0.04)}`,
            borderBottom: `1px solid ${rgba(col, 0.10)}`,
          }}>
            {/* Zone badge */}
            <span style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: '0.10em',
              textTransform: 'uppercase',
              padding: '3px 10px',
              borderRadius: 3,
              background: rgba(col, 0.10),
              border: `1px solid ${rgba(col, 0.30)}`,
              color: col,
              flexShrink: 0,
            }}>
              {node.zone}
            </span>

            {/* Node label */}
            <span style={{
              fontFamily: "'Space Grotesk', sans-serif",
              fontSize: 14,
              fontWeight: 500,
              color: '#e8eaf0',
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              letterSpacing: '-0.01em',
            }}
              title={node.label}
            >
              {node.label}
            </span>

            {/* Close button */}
            <button
              onClick={onClose}
              aria-label="Close node drawer"
              style={{
                background: 'none',
                border: 'none',
                color: '#4a4d5a',
                cursor: 'pointer',
                fontSize: 14,
                lineHeight: 1,
                padding: '4px 6px',
                borderRadius: 3,
                flexShrink: 0,
                transition: `color 150ms var(--ease-premium, ease), background 150ms var(--ease-premium, ease)`,
              }}
              onMouseEnter={e => {
                const b = e.currentTarget as HTMLButtonElement
                b.style.color = '#e8eaf0'
                b.style.background = 'rgba(255,255,255,0.05)'
              }}
              onMouseLeave={e => {
                const b = e.currentTarget as HTMLButtonElement
                b.style.color = '#4a4d5a'
                b.style.background = 'none'
              }}
            >
              ✕
            </button>
          </div>

          {/* ── Body ── */}
          <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>

            {/* ── Left panel: access history + connection chips ── */}
            <div style={{
              width: '42%',
              flexShrink: 0,
              borderRight: `1px solid ${rgba(col, 0.07)}`,
              background: 'rgba(0,0,0,0.15)',
              display: 'flex',
              flexDirection: 'column',
              padding: '12px 10px',
              gap: 4,
              overflow: 'hidden',
            }}>

              {/* Access history */}
              <div style={sectionLabel(col)}>
                Accessi&nbsp;
                <span style={{ color: rgba(col, 0.5), fontWeight: 400 }}>
                  ({detail?.access_history?.length ?? '…'})
                </span>
              </div>

              <div style={{
                flex: 1,
                overflowY: 'auto',
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
                minHeight: 0,
                scrollbarWidth: 'thin',
                scrollbarColor: `${rgba(col, 0.10)} transparent`,
              }}>
                {loading && <LoadingRow />}
                {!loading && detail && detail.access_history.length === 0 && (
                  <EmptyRow text="nessun accesso" />
                )}
                {!loading && detail && detail.access_history.map((h, i) => (
                  <div
                    key={i}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 3,
                      padding: '6px 8px',
                      borderRadius: 5,
                      background: 'var(--bg-s2, #111318)',
                      border: `1px solid ${rgba(col, 0.07)}`,
                    }}
                  >
                    <span style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 10,
                      fontWeight: 600,
                      letterSpacing: '0.08em',
                      textTransform: 'uppercase',
                      color: rgba(col, 0.65),
                    }}>
                      {h.agent}
                    </span>
                    {h.query_text && (
                      <span style={{
                        fontFamily: "'Space Grotesk', sans-serif",
                        fontSize: 12,
                        color: '#8B8D98',
                        lineHeight: 1.4,
                        overflow: 'hidden',
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                      }}>
                        {h.query_text.length > 64
                          ? h.query_text.slice(0, 62) + '…'
                          : h.query_text}
                      </span>
                    )}
                    <span style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 10,
                      color: '#3a3d47',
                      letterSpacing: '0.04em',
                    }}>
                      {new Date(h.queried_at).toLocaleTimeString('it-IT', {
                        hour: '2-digit', minute: '2-digit',
                      })}
                    </span>
                  </div>
                ))}
              </div>

              {/* Connection chips */}
              {connectedNodes.length > 0 && (
                <>
                  <div style={{ ...sectionLabel(col), marginTop: 12, flexShrink: 0 }}>
                    Connessioni&nbsp;
                    <span style={{ color: rgba(col, 0.5), fontWeight: 400 }}>
                      ({node.connections ?? connectedNodes.length})
                    </span>
                  </div>
                  <div style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 4,
                    paddingTop: 2,
                    flexShrink: 0,
                  }}>
                    {connectedNodes.map(cn => {
                      const nc = zoneColor(cn.zone)
                      return (
                        <span
                          key={cn.id}
                          style={{
                            fontFamily: "'Space Grotesk', sans-serif",
                            fontSize: 11,
                            fontWeight: 500,
                            padding: '3px 8px',
                            borderRadius: 3,
                            border: `1px solid ${rgba(nc, 0.30)}`,
                            color: rgba(nc, 0.80),
                            letterSpacing: '0.02em',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            maxWidth: '100%',
                            background: rgba(nc, 0.06),
                          }}
                          title={cn.label}
                        >
                          {cn.label.length > 20 ? cn.label.slice(0, 18) + '…' : cn.label}
                        </span>
                      )
                    })}
                  </div>
                </>
              )}

            </div>

            {/* ── Right panel: document + metadata ── */}
            <div style={{
              flex: 1,
              overflowY: 'auto',
              padding: '12px 14px',
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
              minHeight: 0,
              scrollbarWidth: 'thin',
              scrollbarColor: `${rgba(col, 0.10)} transparent`,
            }}>

              {/* Collection badge + section label */}
              <div style={{ ...sectionLabel(col), alignItems: 'center' }}>
                <span>Contenuto</span>
                <span style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 10,
                  color: rgba(col, 0.45),
                  letterSpacing: '0.06em',
                  marginLeft: 'auto',
                }}>
                  {node.collection}
                </span>
              </div>

              {/* Document text */}
              <div style={{
                fontFamily: "'Space Grotesk', sans-serif",
                fontSize: 13,
                fontWeight: 400,
                color: '#8B8D98',
                lineHeight: 1.65,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                flex: 1,
              }}>
                {loading && <LoadingRow />}
                {!loading && (
                  detail?.document
                    || node.document
                    || <span style={{ color: '#3a3d47', fontStyle: 'italic' }}>nessun contenuto</span>
                )}
              </div>

              {/* Metadata table */}
              {detail && Object.keys(detail.metadata).length > 0 && (
                <div style={{
                  borderTop: `1px solid ${rgba(col, 0.08)}`,
                  paddingTop: 10,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 5,
                  flexShrink: 0,
                }}>
                  {Object.entries(detail.metadata).slice(0, 8).map(([k, v]) => (
                    <div key={k} style={{
                      display: 'flex',
                      gap: 10,
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 11,
                    }}>
                      <span style={{
                        color: '#3a3d47',
                        flexShrink: 0,
                        minWidth: 68,
                        textTransform: 'lowercase',
                        letterSpacing: '0.04em',
                      }}>
                        {k}
                      </span>
                      <span style={{
                        color: '#5a5d6a',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        letterSpacing: '0.02em',
                      }}>
                        {String(v).slice(0, 52)}
                        {String(v).length > 52 && '…'}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Node ID footer */}
              <div style={{
                borderTop: `1px solid rgba(255,255,255,0.04)`,
                paddingTop: 8,
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 9,
                color: '#272930',
                letterSpacing: '0.06em',
                flexShrink: 0,
              }}>
                {node.id}
              </div>

            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

/* ── Style helpers ──────────────────────────────────────────────────────── */

function sectionLabel(col: string): React.CSSProperties {
  return {
    fontFamily:    "'JetBrains Mono', monospace",
    fontSize:      10,
    fontWeight:    600,
    letterSpacing: '0.12em',
    textTransform: 'uppercase',
    color:         rgba(col, 0.55),
    flexShrink:    0,
    display:       'flex',
    alignItems:    'center',
    gap:           6,
  }
}
