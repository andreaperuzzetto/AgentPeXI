/**
 * PersonalView — placeholder FE-1
 * Implementazione completa in FE-6 (Personal + System).
 */
export function PersonalView() {
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
        color: 'var(--zone-personal)',
        opacity: 0.6,
      }}>
        [ PERSONAL · FE-6 ]
      </span>
      <span style={{
        fontFamily: 'var(--fmo)',
        fontSize: 13,
        color: 'var(--tf)',
        opacity: 0.4,
      }}>
        Remind · Recall · Personal context
      </span>
    </div>
  )
}
