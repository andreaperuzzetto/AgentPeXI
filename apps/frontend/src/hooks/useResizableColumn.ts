import { useState, useRef, useCallback, useEffect } from 'react'

const MIN_WIDTH = 280
const MAX_WIDTH_RATIO = 0.6  // 60vw

/**
 * Hook per colonna destra ridimensionabile via drag.
 * - Drag immediato (nessuna transizione CSS durante il drag — evita lag)
 * - snapTo() anima la transizione con var(--e-out)
 */
export function useResizableColumn(defaultWidth: number) {
  const [width, setWidth] = useState(defaultWidth)
  const [transitioning, setTransitioning] = useState(false)
  const isResizing = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(0)
  const transitionTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  const maxWidth = () => Math.floor(window.innerWidth * MAX_WIDTH_RATIO)

  const clamp = (v: number) => Math.min(maxWidth(), Math.max(MIN_WIDTH, v))

  /** Attacca al mousedown sull'handle di resize */
  const onHandleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isResizing.current = true
    startX.current = e.clientX
    startWidth.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }, [width])

  useEffect(() => {
    function onMouseMove(e: MouseEvent) {
      if (!isResizing.current) return
      // Drag verso sinistra → colonna più larga (delta positivo)
      const delta = startX.current - e.clientX
      setWidth(clamp(startWidth.current + delta))
    }

    function onMouseUp() {
      if (!isResizing.current) return
      isResizing.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
    return () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  /** Snap animato a una larghezza target — usato al cambio dominio */
  const snapTo = useCallback((targetWidth: number) => {
    clearTimeout(transitionTimer.current)
    setTransitioning(true)
    setWidth(clamp(targetWidth))
    transitionTimer.current = setTimeout(() => setTransitioning(false), 440)
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  return { width, transitioning, onHandleMouseDown, snapTo }
}
