/**
 * SectionsPanel — A.1
 * Mostra 4 sezioni Etsy con listing_count, last_listing_at relativo,
 * badge arancione se ci sono pending uncategorized.
 * Click → filtra NicheTable + ProductionPipeline via store.etsyView.activeSectionKey
 */
import { useEffect, useState } from 'react'
import { useStore } from '../../store'

interface SectionData {
  section_id: string
  section_name: string
  listing_count: number
  last_listing_at: string | null
  pending_uncategorized: number
}

function relativeTime(iso: string | null): string {
  if (!iso) return 'mai'
  const ms = Date.now() - new Date(iso).getTime()
  const days = Math.floor(ms / 86_400_000)
  if (days === 0) return 'oggi'
  if (days === 1) return 'ieri'
  if (days < 30) return `${days}gg fa`
  const months = Math.floor(days / 30)
  return `${months}me fa`
}

const SECTION_COLORS: Record<string, string> = {
  default: '#6366F1',
}

function sectionColor(name: string): string {
  const lower = name.toLowerCase()
  if (lower.includes('party') || lower.includes('celebration') || lower.includes('wedding')) return '#F59E0B'
  if (lower.includes('wellness') || lower.includes('self') || lower.includes('care')) return '#10B981'
  if (lower.includes('planner') || lower.includes('organizer')) return '#6366F1'
  if (lower.includes('kid') || lower.includes('learn') || lower.includes('school')) return '#EC4899'
  return SECTION_COLORS.default
}

export function SectionsPanel() {
  const [sections, setSections] = useState<SectionData[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const activeSectionKey = useStore((s) => s.etsyView.activeSectionKey)
  const setActiveSection = useStore((s) => s.setEtsyActiveSection)

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const resp = await fetch('/api/etsy/sections', {
          signal: controller.signal,
          headers: { 'X-Personal-Key': import.meta.env.VITE_PERSONAL_KEY ?? '' },
        })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = await resp.json()
        setSections(data.sections ?? [])
      } catch (e) {
        if (e instanceof Error && e.name === 'AbortError') return
        setError(e instanceof Error ? e.message : 'Errore')
      } finally {
        setLoading(false)
      }
    }
    load()
    return () => { controller.abort() }
  }, [])

  if (loading) {
    return (
      <div style={{ padding: '12px 0' }}>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} style={{
            height: 72, borderRadius: 8, background: 'rgba(255,255,255,0.04)',
            marginBottom: 8, animation: 'pulse 1.4s infinite',
          }} />
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ color: '#EF4444', fontSize: 12, padding: 12 }}>
        Sezioni non disponibili
      </div>
    )
  }

  if (sections.length === 0) {
    return (
      <div style={{ color: '#8B8D98', fontSize: 12, padding: 12 }}>
        Nessuna sezione Etsy sincronizzata.
        <br />Usa <code>/sections</code> su Telegram.
      </div>
    )
  }

  // pending_uncategorized is a global shop count, identical on every row (embedded by the backend query)
  const pendingCount = sections[0]?.pending_uncategorized ?? 0

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: '#8B8D98', textTransform: 'uppercase', letterSpacing: 1 }}>
          Sezioni
        </span>
        {pendingCount > 0 && (
          <span style={{
            background: 'rgba(245,158,11,0.14)', color: '#F59E0B',
            fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
          }}>
            {pendingCount} da mappare
          </span>
        )}
      </div>

      {/* Section cards */}
      {sections.map((sec) => {
        const isActive = activeSectionKey === sec.section_id
        const color = sectionColor(sec.section_name)
        return (
          <div
            key={sec.section_id}
            onClick={() => setActiveSection(isActive ? null : sec.section_id)}
            style={{
              padding: '10px 12px',
              borderRadius: 8,
              marginBottom: 6,
              cursor: 'pointer',
              background: isActive ? `${color}22` : 'rgba(255,255,255,0.04)',
              border: `1px solid ${isActive ? color : 'rgba(255,255,255,0.06)'}`,
              transition: 'background 0.15s, border 0.15s',
            }}
          >
            {/* Top row: name */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <span style={{
                fontSize: 12, fontWeight: 600,
                color: isActive ? color : '#E2E4ED',
              }}>
                {sec.section_name}
              </span>
            </div>
            {/* Bottom row: listing count + last listing */}
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 10, color: '#8B8D98' }}>
                {sec.listing_count} listing
              </span>
              <span style={{ fontSize: 10, color: '#8B8D98' }}>
                {relativeTime(sec.last_listing_at)}
              </span>
            </div>
          </div>
        )
      })}

      {/* All sections link */}
      {activeSectionKey && (
        <button
          onClick={() => setActiveSection(null)}
          style={{
            marginTop: 4, width: '100%', padding: '6px 0',
            background: 'transparent', border: '1px dashed rgba(255,255,255,0.12)',
            borderRadius: 6, color: '#8B8D98', fontSize: 11, cursor: 'pointer',
          }}
        >
          Mostra tutte le sezioni
        </button>
      )}
    </div>
  )
}
