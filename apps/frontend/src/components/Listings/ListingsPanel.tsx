import { useState, useEffect } from 'react'

interface Listing {
  id: string
  title: string
  views: number
  favorites: number
  sales: number
  revenue?: number | null
  revenue_eur?: number | null
  price_eur?: number | null
  status?: string
}

function statusTag(status?: string): { label: string; color: string } {
  const s = (status ?? '').toLowerCase()
  if (s === 'active' || s === 'live') return { label: 'LIVE', color: 'var(--ok)' }
  if (s === 'draft') return { label: 'DRAFT', color: 'var(--accent)' }
  if (s === 'inactive') return { label: 'INATTIVO', color: 'var(--tf)' }
  return { label: (status ?? 'DRAFT').toUpperCase(), color: 'var(--tm)' }
}

export function ListingsPanel() {
  const [listings, setListings] = useState<Listing[]>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    const fetchListings = () =>
      fetch('/api/listings')
        .then((r) => (r.ok ? r.json() : { listings: [] }))
        .then((data) => {
          const items = data?.listings ?? (Array.isArray(data) ? data : [])
          setListings(items)
          setLoaded(true)
        })
        .catch(() => { setListings([]); setLoaded(true) })

    fetchListings()
    const id = setInterval(fetchListings, 30_000)
    return () => clearInterval(id)
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

      {/* Panel header */}
      <div className="panel-header">
        <span className="section-label">Listing</span>
        <span
          style={{
            fontFamily: 'var(--fd)',
            fontSize: 12,
            color: 'var(--tf)',
          }}
        >
          {loaded
            ? (() => {
                const live = listings.filter(l => ['active', 'live'].includes((l.status ?? '').toLowerCase())).length
                const draft = listings.filter(l => (l.status ?? 'draft').toLowerCase() === 'draft').length
                return `${live} live · ${draft} draft`
              })()
            : '—'}
        </span>
      </div>

      {/* List area */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '9px 11px',
          display: 'flex',
          flexDirection: 'column',
          gap: 7,
        }}
      >
        {loaded && listings.length === 0 ? (
          <div style={{ padding: '16px 12px', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            <svg width="32" height="32" viewBox="0 0 20 20" fill="none" style={{ color: 'var(--tf)', opacity: 0.5 }}>
              <path d="M5 3h7l4 4v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
              <path d="M12 3v4h4" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
            </svg>
            <span style={{ fontFamily: 'var(--fd)', fontSize: 13, color: 'var(--tf)' }}>
              Nessun listing nel DB locale
            </span>
            <span
              style={{
                fontFamily: 'var(--fd)',
                fontSize: 11,
                color: 'var(--tf)',
                letterSpacing: '0.05em',
                padding: '5px 11px',
                border: '1px solid var(--b0)',
                borderRadius: 5,
                display: 'inline-block',
                marginTop: 4,
              }}
            >
              IN ATTESA PIPELINE
            </span>
          </div>
        ) : (
          /* Loaded listings — .lcard style */
          listings.map((l) => {
            const tag = statusTag(l.status)
            const price = (l.revenue ?? l.revenue_eur ?? l.price_eur ?? 0)
            return (
              <div
                key={l.id}
                className="card"
                style={{ padding: '10px 12px', display: 'flex', gap: 9, alignItems: 'flex-start' }}
              >
                {/* .lthumb */}
                <div
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: 6,
                    flexShrink: 0,
                    background: 'var(--s3)',
                    border: '1px solid var(--b0)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <svg width="18" height="18" viewBox="0 0 20 20" fill="none" style={{ color: 'var(--tm)' }}>
                    <path d="M5 3h7l4 4v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
                    <path d="M12 3v4h4" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
                    <path d="M7 10h6M7 13h4" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
                  </svg>
                </div>
                {/* .linfo */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 15,
                      fontWeight: 500,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap' as const,
                      color: 'var(--tp)',
                    }}
                  >
                    {l.title}
                  </div>
                  <div
                    style={{
                      fontFamily: 'var(--fd)',
                      fontSize: 13,
                      color: 'var(--tm)',
                      marginTop: 2,
                    }}
                  >
                    {(l.views ?? 0).toLocaleString('it-IT')} views · {l.favorites ?? 0} ♥ · {l.sales ?? 0} vendite
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 4 }}>
                    <span
                      style={{
                        width: 5,
                        height: 5,
                        borderRadius: '50%',
                        background: tag.color,
                      }}
                    />
                    <span
                      style={{
                        fontFamily: 'var(--fd)',
                        fontSize: 11,
                        color: tag.color,
                        letterSpacing: '0.04em',
                      }}
                    >
                      {tag.label}
                    </span>
                  </div>
                </div>
                {/* .lprice */}
                <div
                  style={{
                    fontFamily: 'var(--fd)',
                    fontSize: 16,
                    color: 'var(--accent)',
                    fontWeight: 500,
                    flexShrink: 0,
                  }}
                >
                  €{(price as number).toFixed(2)}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}


