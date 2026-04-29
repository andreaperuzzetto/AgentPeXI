/**
 * AnalyticsView — placeholder FE-1
 * Implementazione completa in FE-5 (AnalyticsView).
 */
export function AnalyticsView() {
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
        color: 'var(--zone-analytics)',
        opacity: 0.6,
      }}>
        [ ANALYTICS · FE-5 ]
      </span>
      <span style={{
        fontFamily: 'var(--fmo)',
        fontSize: 13,
        color: 'var(--tf)',
        opacity: 0.4,
      }}>
        Finance · CTR A/B · Ladder · Token cost
      </span>
    </div>
  )
}
