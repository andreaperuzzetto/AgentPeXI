/**
 * EtsyView — placeholder FE-1
 * Implementazione completa in FE-4 (EtsyView).
 */
export function EtsyView() {
  return (
    <div style={{
      width: '100%',
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 12,
    }}>
      <span style={{
        fontFamily: 'var(--fmo)',
        fontSize: 11,
        letterSpacing: '0.2em',
        textTransform: 'uppercase',
        color: 'var(--zone-etsy)',
        opacity: 0.6,
      }}>
        [ ETSY · FE-4 ]
      </span>
      <span style={{
        fontFamily: 'var(--fmo)',
        fontSize: 13,
        color: 'var(--tf)',
        opacity: 0.4,
      }}>
        Production pipeline · Niche intelligence · Ads status
      </span>
    </div>
  )
}
