/**
 * ShopIdentityPanel — A.2/T8
 * Displays the active shop brand identity (aesthetic, palette, mockup style, tone, logo).
 * Shows in EtsyView right sidebar.
 */
import { useEffect, useState } from 'react'
import type { ShopIdentity } from '../../types'

const BASE = import.meta.env.VITE_API_BASE ?? ''

export function ShopIdentityPanel() {
  const [identity, setIdentity] = useState<ShopIdentity | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    const ac = new AbortController()

    async function fetchIdentity() {
      try {
        const resp = await fetch(`${BASE}/api/etsy/shop-identity`, { signal: ac.signal })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = await resp.json()
        if (!cancelled) setIdentity(data.identity ?? null)
      } catch (err) {
        if (!cancelled) console.warn('ShopIdentityPanel fetch error:', err)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchIdentity()
    return () => { cancelled = true; ac.abort() }
  }, [])

  if (loading) return (
    <>
      <style>{`
        .shop-identity-panel {
          padding: 12px;
          background: var(--surface-secondary, #1a1a1a);
          border-radius: 8px;
          margin-bottom: 12px;
        }
        .shop-identity-panel--loading .shop-identity-panel__skeleton {
          height: 72px;
          background: linear-gradient(90deg, #2a2a2a 25%, #333 50%, #2a2a2a 75%);
          border-radius: 6px;
          animation: pulse 1.4s infinite;
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
      `}</style>
      <div className="shop-identity-panel shop-identity-panel--loading">
        <div className="shop-identity-panel__skeleton" />
      </div>
    </>
  )

  if (!identity) return (
    <>
      <style>{`
        .shop-identity-panel {
          padding: 12px;
          background: var(--surface-secondary, #1a1a1a);
          border-radius: 8px;
          margin-bottom: 12px;
        }
        .shop-identity-panel--empty {
          display: flex;
          flex-direction: column;
          gap: 4px;
          opacity: 0.6;
        }
        .shop-identity-panel__empty-label { font-weight: 600; font-size: 13px; }
        .shop-identity-panel__hint { font-size: 11px; color: #888; }
      `}</style>
      <div className="shop-identity-panel shop-identity-panel--empty">
        <span className="shop-identity-panel__empty-label">No brand identity active</span>
        <span className="shop-identity-panel__hint">Use /style_guide on Telegram</span>
      </div>
    </>
  )

  return (
    <>
      <style>{`
        .shop-identity-panel {
          padding: 12px;
          background: var(--surface-secondary, #1a1a1a);
          border-radius: 8px;
          margin-bottom: 12px;
        }
        .shop-identity-panel__header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 8px;
        }
        .shop-identity-panel__title { font-weight: 700; font-size: 13px; }
        .shop-identity-panel__badge {
          font-size: 10px;
          padding: 2px 6px;
          background: #2a3a2a;
          color: #7ec87e;
          border-radius: 4px;
          text-transform: uppercase;
        }
        .shop-identity-panel__palette {
          display: flex;
          gap: 6px;
          margin-bottom: 8px;
        }
        .shop-identity-panel__swatch {
          width: 24px;
          height: 24px;
          border-radius: 50%;
          border: 1px solid rgba(255,255,255,0.15);
          cursor: default;
        }
        .shop-identity-panel__tone {
          font-size: 11px;
          color: #aaa;
          line-height: 1.4;
          margin: 0 0 8px 0;
        }
        .shop-identity-panel__logo {
          margin-top: 8px;
          border-radius: 6px;
          object-fit: cover;
        }
      `}</style>
      <div className="shop-identity-panel">
        <div className="shop-identity-panel__header">
          <span className="shop-identity-panel__title">{identity.aesthetic_name}</span>
          <span className="shop-identity-panel__badge">{identity.mockup_style}</span>
        </div>

        <div className="shop-identity-panel__palette">
          {[identity.palette_primary, identity.palette_secondary, identity.palette_accent].map((hex) => (
            <div
              key={hex}
              className="shop-identity-panel__swatch"
              style={{ backgroundColor: hex }}
              title={hex}
            />
          ))}
        </div>

        <p className="shop-identity-panel__tone">{identity.tone}</p>

        {identity.logo_path && (
          <img
            src={`${BASE}/${identity.logo_path}`}
            alt="Shop logo"
            className="shop-identity-panel__logo"
            width={64}
            height={64}
          />
        )}
      </div>
    </>
  )
}
