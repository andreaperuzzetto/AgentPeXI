import { useState } from 'react'
import { TaskDrawer } from '../TaskDetail/TaskDrawer'

const MOCK_LISTINGS = [
  { id: 'L001', title: '2025 Digital Planner – Minimalist', views: 842, favorites: 63, sales: 12, revenue: 95.88 },
  { id: 'L002', title: 'Wedding Budget Tracker Printable', views: 1204, favorites: 97, sales: 24, revenue: 167.76 },
  { id: 'L003', title: 'Habit Tracker SVG Bundle – Cricut', views: 376, favorites: 28, sales: 5, revenue: 24.75 },
]

const COL_STYLE: React.CSSProperties = {
  padding: '6px 10px',
  fontSize: '0.75rem',
  textAlign: 'right',
  whiteSpace: 'nowrap',
}

const COL_STYLE_LEFT: React.CSSProperties = { ...COL_STYLE, textAlign: 'left' }

export function ListingsPanel() {
  const [selectedId, setSelectedId] = useState<string | null>(null)

  return (
    <section>
      <div style={{ padding: '10px 12px 6px' }}>
        <span className="section-label">Listings</span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: '0.75rem',
          }}
        >
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-subtle)' }}>
              <th style={{ ...COL_STYLE_LEFT, color: 'var(--text-muted)', fontWeight: 500 }}>Titolo</th>
              <th style={{ ...COL_STYLE, color: 'var(--text-muted)', fontWeight: 500 }}>Views</th>
              <th style={{ ...COL_STYLE, color: 'var(--text-muted)', fontWeight: 500 }}>Favorites</th>
              <th style={{ ...COL_STYLE, color: 'var(--text-muted)', fontWeight: 500 }}>Sales</th>
              <th style={{ ...COL_STYLE, color: 'var(--text-muted)', fontWeight: 500 }}>Revenue</th>
            </tr>
          </thead>
          <tbody>
            {MOCK_LISTINGS.map((l) => (
              <tr
                key={l.id}
                onClick={() => setSelectedId(selectedId === l.id ? null : l.id)}
                style={{
                  borderBottom: '1px solid var(--border-subtle)',
                  cursor: 'pointer',
                  background: selectedId === l.id ? 'var(--bg-surface-3)' : 'transparent',
                  transition: 'background 100ms',
                }}
                onMouseEnter={(e) => {
                  if (selectedId !== l.id)
                    (e.currentTarget as HTMLTableRowElement).style.background = 'var(--bg-surface-2)'
                }}
                onMouseLeave={(e) => {
                  if (selectedId !== l.id)
                    (e.currentTarget as HTMLTableRowElement).style.background = 'transparent'
                }}
              >
                <td style={{ ...COL_STYLE_LEFT, color: 'var(--text-primary)' }}>{l.title}</td>
                <td style={COL_STYLE}><span className="font-data">{l.views.toLocaleString('it-IT')}</span></td>
                <td style={COL_STYLE}><span className="font-data">{l.favorites}</span></td>
                <td style={COL_STYLE}><span className="font-data">{l.sales}</span></td>
                <td style={COL_STYLE}>
                  <span className="font-data" style={{ color: 'var(--accent)' }}>
                    ${l.revenue.toFixed(2)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Task Detail Drawer */}
      {selectedId && (
        <TaskDrawer listingId={selectedId} onClose={() => setSelectedId(null)} />
      )}
    </section>
  )
}
