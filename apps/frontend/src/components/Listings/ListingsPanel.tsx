import { useState, useEffect } from 'react'

interface Listing {
  id: string
  title: string
  views: number
  favorites: number
  sales: number
  revenue: number
  status?: string
}

export function ListingsPanel() {
  const [listings, setListings] = useState<Listing[]>([])
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    fetch('/api/listings')
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => { setListings(Array.isArray(data) ? data : []); setLoaded(true) })
      .catch(() => { setListings([]); setLoaded(true) })
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>

      {/* Panel header */}
      <div className="panel-header">
        <span className="section-label">Listing</span>
        <span
          style={{
            fontFamily: 'var(--fd)',
            fontSize: 10,
            color: 'var(--tf)',
          }}
        >
          {loaded ? `${listings.length} live · 0 draft` : '—'}
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
          /* Draft / pending state — matches prototype listing block */
          <>
            <DraftCard
              title="Botanical Wall Art — Summer Set"
              meta="4 file · alta risoluzione"
              price="€18"
              status="DRAFT"
              statusColor="var(--warn)"
              opacity={0.55}
            />
            <DraftCard
              title="Abstract Minimalism Bundle"
              meta="5 varianti · bundle"
              price="€22"
              status="IN CODA"
              statusColor="var(--tf)"
              opacity={0.35}
            />
            <div style={{ padding: '10px 12px', textAlign: 'center', marginTop: 2 }}>
              <span
                style={{
                  fontFamily: 'var(--fd)',
                  fontSize: 9,
                  color: 'var(--tf)',
                  letterSpacing: '0.05em',
                  padding: '5px 11px',
                  border: '1px solid var(--b0)',
                  borderRadius: 5,
                  display: 'inline-block',
                }}
              >
                ETSY API — PENDING APPROVAL
              </span>
            </div>
          </>
        ) : (
          /* Loaded listings — .lcard style */
          listings.map((l) => (
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
                    fontSize: 13,
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
                    fontSize: 11,
                    color: 'var(--tm)',
                    marginTop: 2,
                  }}
                >
                  {l.views.toLocaleString('it-IT')} views · {l.favorites} ♥ · {l.sales} vendite
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 4 }}>
                  <span
                    style={{
                      width: 5,
                      height: 5,
                      borderRadius: '50%',
                      background: 'var(--ok)',
                    }}
                  />
                  <span
                    style={{
                      fontFamily: 'var(--fd)',
                      fontSize: 9,
                      color: 'var(--ok)',
                      letterSpacing: '0.04em',
                    }}
                  >
                    LIVE
                  </span>
                </div>
              </div>
              {/* .lprice */}
              <div
                style={{
                  fontFamily: 'var(--fd)',
                  fontSize: 14,
                  color: 'var(--accent)',
                  fontWeight: 500,
                  flexShrink: 0,
                }}
              >
                ${l.revenue.toFixed(2)}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function DraftCard({
  title,
  meta,
  price,
  status,
  statusColor,
  opacity,
}: {
  title: string
  meta: string
  price: string
  status: string
  statusColor: string
  opacity: number
}) {
  return (
    <div
      className="card"
      style={{ padding: '10px 12px', display: 'flex', gap: 9, alignItems: 'flex-start', opacity }}
    >
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
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 500,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap' as const,
            color: 'var(--tp)',
          }}
        >
          {title}
        </div>
        <div
          style={{ fontFamily: 'var(--fd)', fontSize: 11, color: 'var(--tm)', marginTop: 2 }}
        >
          {meta}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 4 }}>
          <span style={{ width: 5, height: 5, borderRadius: '50%', background: statusColor }} />
          <span
            style={{ fontFamily: 'var(--fd)', fontSize: 9, color: statusColor, letterSpacing: '0.04em' }}
          >
            {status}
          </span>
        </div>
      </div>
      <div
        style={{
          fontFamily: 'var(--fd)',
          fontSize: 14,
          color: 'var(--accent)',
          fontWeight: 500,
          flexShrink: 0,
        }}
      >
        {price}
      </div>
    </div>
  )
}
