import { useState, useEffect, useRef } from 'react'

export function useTypewriter(text: string, active: boolean, speed = 18): string {
  const [displayed, setDisplayed] = useState(active ? '' : text)
  const idx = useRef(0)

  useEffect(() => {
    if (!active) {
      setDisplayed(text)
      return
    }
    idx.current = 0
    setDisplayed('')
    const id = setInterval(() => {
      idx.current++
      if (idx.current >= text.length) {
        setDisplayed(text)
        clearInterval(id)
      } else {
        setDisplayed(text.slice(0, idx.current))
      }
    }, speed)
    return () => clearInterval(id)
  }, [text, active, speed])

  return displayed
}
