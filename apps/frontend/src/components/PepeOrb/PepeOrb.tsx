import { useRef, useEffect, useCallback } from 'react'
import { useUiStore, type OrbState } from '../../store/uiStore'
import './PepeOrb.css'

/* ── Orb state labels (da changes.md §11.2) ─────────────────── */
const LABEL: Record<OrbState, string> = {
  wakeword:  '',
  listening: 'In ascolto…',
  thinking:  'Elaboro…',
  speaking:  '',
}

/* ── Debug buttons (labels visibili ai tasti manuali) ───────── */
const DEBUG_STATES: OrbState[] = ['wakeword', 'listening', 'thinking', 'speaking']
const DEBUG_LABELS: Record<OrbState, string> = {
  wakeword:  'Standby',
  listening: 'Listen',
  thinking:  'Think',
  speaking:  'Speak',
}

/* ── Particle sphere generation ──────────────────────────────── */
const TOTAL = 300

function generateParticles(container: HTMLDivElement) {
  document.getElementById('orb-styles')?.remove()

  const css: string[] = []
  for (let i = 1; i <= TOTAL; i++) {
    const sz  = (3.5 + Math.random() * 3.0).toFixed(1)
    const hue = (128 + Math.random() * 28).toFixed(1)
    const sat = (88  + Math.random() * 12).toFixed(0)
    const lit = (48  + Math.random() * 10).toFixed(0)
    const del = (-(Math.random() * 22)).toFixed(2)
    const z   = (Math.random() * 360).toFixed(2)
    const y   = (Math.random() * 360).toFixed(2)

    css.push(
      `.particle:nth-child(${i}){` +
        `width:${sz}px;height:${sz}px;` +
        `background:hsl(${hue},${sat}%,${lit}%);` +
        `box-shadow:0 0 2px 0px hsl(${hue},90%,62%);` +
        `animation:orb${i} var(--time,22s) infinite;` +
        `animation-delay:${del}s` +
      `}` +
      `@keyframes orb${i}{` +
        `20%{opacity:var(--opacity-pk,.72)}` +
        `30%{transform:rotateZ(-${z}deg) rotateY(${y}deg) translateX(var(--orb-size,140px)) rotateZ(${z}deg)}` +
        `80%{opacity:var(--opacity-pk,.72);transform:rotateZ(-${z}deg) rotateY(${y}deg) translateX(var(--orb-size,140px)) rotateZ(${z}deg)}` +
        `100%{transform:rotateZ(-${z}deg) rotateY(${y}deg) translateX(calc(var(--orb-size,140px)*3)) rotateZ(${z}deg);opacity:0}` +
      `}`
    )
  }

  const styleEl = document.createElement('style')
  styleEl.id = 'orb-styles'
  styleEl.textContent = css.join('')
  document.head.appendChild(styleEl)

  for (let i = 0; i < TOTAL; i++) {
    const p = document.createElement('div')
    p.className = 'particle'
    container.appendChild(p)
  }
}

/* ── Constants ───────────────────────────────────────────────── */
const UTTERANCE_TIMEOUT_MS = 8_000   // max durata registrazione utterance (§11.4)
const CHUNK_INTERVAL_MS    =   500   // intervallo chunk wake word

/* ── Component ───────────────────────────────────────────────── */
export function PepeOrb() {
  const orbState    = useUiStore((s) => s.orbState)
  const setOrbState = useUiStore((s) => s.setOrbState)

  /* ── Refs ── */
  const innerRef  = useRef<HTMLDivElement>(null)
  const wsRef     = useRef<WebSocket | null>(null)
  const mediaRef  = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef  = useRef<ReturnType<typeof setTimeout> | null>(null)

  /* ── Particle sphere — generata una sola volta ── */
  useEffect(() => {
    if (innerRef.current) generateParticles(innerRef.current)
  }, [])

  /* ── Helpers audio ── */
  const stopMedia = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
    if (mediaRef.current && mediaRef.current.state !== 'inactive') {
      mediaRef.current.stop()
    }
    mediaRef.current = null
    chunksRef.current = []
  }, [])

  const returnToWakeword = useCallback(() => {
    stopMedia()
    window.speechSynthesis.cancel()
    setOrbState('wakeword')
  }, [stopMedia, setOrbState])

  /** Streaming chunk → WebSocket per wake word detection */
  const startChunkStream = useCallback(async (ws: WebSocket) => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream, { timeslice: CHUNK_INTERVAL_MS })
      mediaRef.current = mr

      mr.ondataavailable = (e) => {
        if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
          e.data.arrayBuffer().then((buf) => ws.send(buf))
        }
      }
      mr.start(CHUNK_INTERVAL_MS)
    } catch (err) {
      console.warn('[PepeOrb] Microfono non disponibile:', err)
    }
  }, [])

  /** Registrazione utterance completa → inviata come blob dopo timeout */
  const startUtteranceRecording = useCallback(async (ws: WebSocket) => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      chunksRef.current = []
      mediaRef.current = mr

      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        chunksRef.current = []
        if (blob.size > 0 && ws.readyState === WebSocket.OPEN) {
          setOrbState('thinking')
          blob.arrayBuffer().then((buf) => ws.send(buf))
        } else {
          returnToWakeword()
        }
        // chiude le tracce audio
        stream.getTracks().forEach((t) => t.stop())
      }

      mr.start()

      // Timeout fisso 8s (§11.4) — poi invia automaticamente
      timerRef.current = setTimeout(() => {
        if (mediaRef.current?.state === 'recording') mediaRef.current.stop()
      }, UTTERANCE_TIMEOUT_MS)

    } catch (err) {
      console.warn('[PepeOrb] Microfono non disponibile:', err)
      returnToWakeword()
    }
  }, [setOrbState, returnToWakeword])

  /* ── WebSocket voice — connessione al mount ── */
  useEffect(() => {
    const wsUrl = `ws://${window.location.host}/ws/voice`
    const ws = new WebSocket(wsUrl)
    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      wsRef.current = ws
      setOrbState('wakeword')
      startChunkStream(ws)
    }

    ws.onmessage = (e) => {
      let msg: { type: string; text?: string; audio_b64?: string | null }
      try { msg = JSON.parse(e.data as string) }
      catch { return }

      if (msg.type === 'wake') {
        // Wake word rilevato → avvia utterance recording
        stopMedia()
        setOrbState('listening')
        startUtteranceRecording(ws)

      } else if (msg.type === 'response') {
        setOrbState('speaking')

        if (msg.audio_b64) {
          // ElevenLabs audio → decode → play
          const bytes = Uint8Array.from(atob(msg.audio_b64), (c) => c.charCodeAt(0))
          const blob  = new Blob([bytes], { type: 'audio/mpeg' })
          const url   = URL.createObjectURL(blob)
          const audio = new Audio(url)
          audio.onended = () => { URL.revokeObjectURL(url); returnToWakeword(); startChunkStream(ws) }
          audio.onerror = () => { URL.revokeObjectURL(url); returnToWakeword(); startChunkStream(ws) }
          audio.play().catch(() => { returnToWakeword(); startChunkStream(ws) })

        } else if (msg.text) {
          // Fallback browser TTS
          const utter   = new SpeechSynthesisUtterance(msg.text)
          utter.lang    = 'it-IT'
          utter.rate    = 0.95
          utter.onend   = () => { returnToWakeword(); startChunkStream(ws) }
          utter.onerror = () => { returnToWakeword(); startChunkStream(ws) }
          window.speechSynthesis.speak(utter)

        } else {
          // Risposta vuota
          returnToWakeword()
          startChunkStream(ws)
        }

      } else if (msg.type === 'error') {
        console.warn('[PepeOrb] Errore voice backend:', msg)
        returnToWakeword()
        startChunkStream(ws)
      }
    }

    ws.onerror = () => { setOrbState('wakeword') }
    ws.onclose = () => { setOrbState('wakeword') }

    return () => {
      stopMedia()
      window.speechSynthesis.cancel()
      ws.close()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* ── Click handler sull'Orb ── */
  const handleOrbClick = useCallback(() => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return

    if (orbState === 'wakeword') {
      // Skip wake word → registra subito
      stopMedia()
      setOrbState('listening')
      startUtteranceRecording(ws)

    } else if (orbState === 'listening') {
      // Ferma registrazione anticipatamente
      if (mediaRef.current?.state === 'recording') mediaRef.current.stop()

    } else if (orbState === 'speaking') {
      // Interrompi audio
      window.speechSynthesis.cancel()
      returnToWakeword()
      startChunkStream(ws)
    }
    // 'thinking': click ignorato — Pepe sta elaborando
  }, [orbState, stopMedia, setOrbState, startUtteranceRecording, returnToWakeword, startChunkStream])

  /* ── Render ── */
  return (
    <>
      {/* atmosphere glow */}
      <div className="orb-atmos" />

      {/* particle sphere — click attiva voice */}
      <div className={`orb-wrap s-${orbState}`} onClick={handleOrbClick} style={{ cursor: 'pointer' }}>
        <div className="orb-inner" ref={innerRef} />
      </div>

      {/* state label */}
      <div className="orb-state-lbl">{LABEL[orbState]}</div>

      {/* debug buttons — bottom-left */}
      <div className="orb-controls">
        {DEBUG_STATES.map((s) => (
          <button
            key={s}
            className={`orb-btn${orbState === s ? ' active' : ''}`}
            onClick={() => setOrbState(s)}
          >
            {DEBUG_LABELS[s]}
          </button>
        ))}
      </div>
    </>
  )
}
