import { useState, useEffect } from 'react'

interface QueueItem {
  id: number
  task_id: string
  product_type: string
  niche: string
  status: 'planned' | 'in_progress' | 'completed' | 'failed' | 'skipped'
  created_at: string
  updated_at?: string | null
  file_paths?: string | null
}

function statusStyle(status: QueueItem['status']): { bg: string; color: string; border: string; label: string } {
  switch (status) {
    case 'in_progress':
      return { bg: 'rgba(45,232,106,.10)', color: 'var(--accent)', border: '1px solid rgba(45,232,106,.3)', label: 'IN CORSO' }
    case 'completed':
      return { bg: 'rgba(45,232,106,.05)', color: 'var(--ok)', border: '1px solid rgba(45,232,106,.15)', label: 'COMPLETATO' }
    case 'failed':
      return { bg: 'rgba(224,82,82,.08)', color: 'var(--err)', border: '1px solid rgba(224,82,82,.2)', label: 'ERRORE' }
    case 'skipped':
      return { bg: 'var(--s2)', color: 'var(--tf)', border: '1px solid var(--b0)', label: 'SALTATO' }
    case 'planned':
    default:
      return { bg: 'rgba(45,232,106,.04)', color: 'var(--tm)', border: '1px solid rgba(45,232,106,.12)', label: 'PIANIFICATO' }
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return '—'
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffH = diffMs / 3_600_000
    if (diffH < 1) return `${Math.round(diffMs / 60_000)}m fa`
    if (diffH < 24) return `${Math.round(diffH)}h fa`
    return d.toLocaleDateString('it-IT', { day: '2-digit', month: 'short' })
  } catch {
    return '—'
  }
}

function productTypeLabel(pt: string): string {
  const map: Record<string, string> = {
    quote_print: 'Quote Print',
    wall_art: 'Wall Art',
    digital_planner: 'Planner',
    sticker_sheet: 'Stickers',
    journal: 'Journal',
    printable: 'Printable',
  }
  return map[pt] ?? pt.replace(/_/g, ' ')
}

export function SchedulerPanel() {
  const [items, setItems] = useState<QueueItem[]>([])
  const [filter, setFilter] = useState<string>('all')

  const fetchQueue = () =>
    fetch('/api/production-queue?limit=50')
      .then((r) => (r.ok ? r.json() : { items: [] }))
      .then((data) => setItems(Array.isArray(data.items) ? data.items : []))
      .catch(() => setItems([]))

  useEffect(() => {
    fetchQueue()
    const id = setInterval(fetchQueue, 20_000)
    return () => clearInterval(id)
  }, [])

  const statusFilters: { key: string; label: string }[] = [
    { key: 'all', label: 'Tutti' },
    { key: 'planned', label: 'Pianificati' },
    { key: 'in_progress', label: 'In corso' },
    { key: 'completed', label: 'Completati' },
    { key: 'failed', label: 'Errori' },
  ]

  const visible = filter === 'all' ? items : items.filter((i) => i.status === filter)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, overflow: 'hidden' }}>
      {/* Header */}
      <div
        style={{
          padding: '8px 13px',
          borderBottom: '1px solid var(--b0)',
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span
          style={{
            fontFamily: 'var(--fh)',
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.08em',
            textTransform: 'uppercase' as const,
            color: 'var(--tm)',
          }}
        >
          Coda Produzione
        </span>
        <span
          style={{
            fontFamily: 'var(--fd)',
            fontSize: 11,
            color: 'var(--tf)',
          }}
        >
          {items.length > 0 ? `${items.length} item${items.length !== 1 ? 's' : ''}` : ''}
        </span>
      </div>

      {/* Filter tabs */}
      {items.length > 0 && (
        <div
          style={{
            display: 'flex',
            gap: 4,
            padding: '6px 13px',
            borderBottom: '1px solid var(--b0)',
            flexShrink: 0,
            overflowX: 'auto',
          }}
        >
          {statusFilters.map(({ key, label }) => {
            const count = key === 'all' ? items.length : items.filter((i) => i.status === key).length
            if (key !== 'all' && count === 0) return null
            const active = filter === key
            return (
              <button
                key={key}
                onClick={() => setFilter(key)}
                style={{
                  fontFamily: 'var(--fd)',
                  fontSize: 11,
                  padding: '2px 7px',
                  borderRadius: 4,
                  border: active ? '1px solid rgba(45,232,106,.35)' : '1px solid var(--b0)',
                  background: active ? 'rgba(45,232,106,.08)' : 'transparent',
                  color: active ? 'var(--accent)' : 'var(--tf)',
                  cursor: 'pointer',
                  whiteSpace: 'nowrap' as const,
                  transition: 'all .15s',
                }}
              >
                {label}{count > 0 ? ` ${count}` : ''}
              </button>
            )
          })}
        </div>
      )}

      {/* List */}
      {visible.length === 0 ? (
        <div style={{ padding: '16px 13px', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
          <svg width="28" height="28" viewBox="0 0 20 20" fill="none" style={{ color: 'var(--tf)', opacity: 0.5 }}>
            <rect x="3" y="3" width="14" height="14" rx="2" stroke="currentColor" strokeWidth="1.2" />
            <path d="M7 7h6M7 10h4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
          </svg>
          <span style={{ fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--tf)' }}>
            {filter === 'all' ? 'Nessun prodotto in coda' : 'Nessun item con questo stato'}
          </span>
        </div>
      ) : (
        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 13px' }}>
          {visible.map((item) => {
            const st = statusStyle(item.status)
            return (
              <QueueRow
                key={item.task_id}
                niche={item.niche}
                productType={productTypeLabel(item.product_type)}
                tagLabel={st.label}
                tagBg={st.bg}
                tagColor={st.color}
                tagBorder={st.border}
                time={formatDate(item.created_at)}
                hasFiles={Boolean(item.file_paths)}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

function QueueRow({
  niche,
  productType,
  tagLabel,
  tagBg,
  tagColor,
  tagBorder,
  time,
  hasFiles,
}: {
  niche: string
  productType: string
  tagLabel: string
  tagBg: string
  tagColor: string
  tagBorder: string
  time: string
  hasFiles: boolean
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '5px 3px',
        fontSize: 14,
        borderRadius: 4,
        transition: 'background .2s var(--e-io)',
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = 'rgba(45,232,106,.04)' }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
    >
      {/* Time */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 11,
          color: 'var(--tf)',
          width: 40,
          flexShrink: 0,
        }}
      >
        {time}
      </span>

      {/* Niche + type */}
      <span style={{ color: 'var(--tp)', flex: 1, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {niche}
        <span style={{ color: 'var(--tf)', fontSize: 11, marginLeft: 5 }}>{productType}</span>
        {hasFiles && (
          <span style={{ marginLeft: 5, fontSize: 10, color: 'var(--ok)', opacity: 0.7 }}>✓</span>
        )}
      </span>

      {/* Status tag */}
      <span
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 11,
          padding: '1px 6px',
          borderRadius: 4,
          flexShrink: 0,
          background: tagBg,
          color: tagColor,
          border: tagBorder,
        }}
      >
        {tagLabel}
      </span>
    </div>
  )
}
