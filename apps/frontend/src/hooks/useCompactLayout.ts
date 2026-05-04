import { useState, useEffect } from 'react'

const BREAKPOINT = 768

export function useCompactLayout(): boolean {
  const [compact, setCompact] = useState(() => window.innerWidth < BREAKPOINT)

  useEffect(() => {
    const fn = () => setCompact(window.innerWidth < BREAKPOINT)
    window.addEventListener('resize', fn)
    return () => window.removeEventListener('resize', fn)
  }, [])

  return compact
}
