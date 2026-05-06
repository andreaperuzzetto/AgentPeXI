/**
 * ShopIdentityPanel — A.2/T8
 * Displays the active shop brand identity (aesthetic, palette, mockup style, tone, logo).
 * Shows in EtsyView right sidebar.
 */
import { useEffect, useState } from 'react'
import type { ShopIdentity } from '../../types'

const BASE = import.meta.env.VITE_API_BASE ?? ''

const panelStyle = { padding: '12px', background: 'var(--surface-secondary, #1a1a1a)', borderRadius: '8px', marginBottom: '12px' }

export function ShopIdentityPanel() {
  const [identity, setIdentity] = useState<ShopIdentity | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const ac = new AbortController()

    async function fetchIdentity() {
      try {
        const resp = await fetch(`${BASE}/api/etsy/shop-identity`, {
          signal: ac.signal,
          headers: { 'X-Personal-Key': import.meta.env.VITE_PERSONAL_KEY ?? '' },
        })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = await resp.json()
        if (!cancelled) setIdentity(data.identity ?? null)
      } catch (err) {
        if (!cancelled) {
          if (err instanceof Error && err.name !== 'AbortError') {
            setError('Failed to load brand identity')
            console.warn('ShopIdentityPanel fetch error:', err)
          }
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchIdentity()
    return () => { cancelled = true; ac.abort() }
  }, [])

  if (loading) return (
    <div style={panelStyle}>
      <div style={{ height: '72px', background: 'linear-gradient(90deg, #2a2a2a 25%, #333 50%, #2a2a2a 75%)', borderRadius: '6px' }} />
    </div>
  )

  if (error) return (
    <div style={panelStyle}>
      <span style={{ fontWeight: 600, fontSize: '13px' }}>⚠️ {error}</span>
    </div>
  )

  if (!identity) return (
    <div style={{ ...panelStyle, display: 'flex', flexDirection: 'column' as const, gap: '4px', opacity: 0.6 }}>
      <span style={{ fontWeight: 600, fontSize: '13px' }}>No brand identity active</span>
      <span style={{ fontSize: '11px', color: '#888' }}>Use /style_guide on Telegram</span>
    </div>
  )

  return (
    <div style={panelStyle}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
        <span style={{ fontWeight: 700, fontSize: '13px' }}>{identity.aesthetic_name}</span>
        <span style={{ fontSize: '10px', padding: '2px 6px', background: '#2a3a2a', color: '#7ec87e', borderRadius: '4px', textTransform: 'uppercase' as const }}>
          {identity.mockup_style.replace(/_/g, ' ')}
        </span>
      </div>

      <div style={{ display: 'flex', gap: '6px', marginBottom: '8px' }}>
        {[identity.palette_primary, identity.palette_secondary, identity.palette_accent].map((hex, i) => (
          <div
            key={i}
            style={{ width: '24px', height: '24px', borderRadius: '50%', border: '1px solid rgba(255,255,255,0.15)', backgroundColor: hex }}
            title={hex}
          />
        ))}
      </div>

      <p style={{ fontSize: '11px', color: '#aaa', lineHeight: 1.4, margin: 0 }}>{identity.tone}</p>

      {identity.logo_path && (
        <img
          src={`${BASE}${identity.logo_path}`}
          alt="Shop logo"
          style={{ marginTop: '8px', borderRadius: '6px', objectFit: 'cover' as const }}
          width={64}
          height={64}
        />
      )}
    </div>
  )
}
