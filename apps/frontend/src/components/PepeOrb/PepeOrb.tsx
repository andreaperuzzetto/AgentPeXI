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
const WAKE_SAMPLE_MS       = 3_000   // durata ogni finestra di registrazione per wake word

/* ── Wake acknowledgment ─────────────────────────────────────── */
const WAKE_ACKS = [
  'Dimmi.',
  'Sì?',
  'Ti ascolto.',
  'Dimmi pure.',
  'Eccomi.',
]

/**
 * Risponde vocalmente con una breve frase italiana per confermare
 * che il wake word è stato sentito e Pepe è in ascolto.
 * Risolve il Promise al termine del parlato, così la mic si apre
 * solo dopo — evitando di catturare la voce di Pepe nell'utterance.
 */
function playWakeAck(): Promise<void> {
  return new Promise((resolve) => {
    try {
      window.speechSynthesis.cancel()
      const phrase = WAKE_ACKS[Math.floor(Math.random() * WAKE_ACKS.length)]
      const utter  = new SpeechSynthesisUtterance(phrase)
      utter.lang   = 'it-IT'
      utter.rate   = 1.05
      utter.pitch  = 1.0
      utter.volume = 0.9
      utter.onend   = () => resolve()
      utter.onerror = () => resolve()   // se TTS non disponibile, va avanti lo stesso
      window.speechSynthesis.speak(utter)
    } catch {
      resolve()
    }
  })
}

/* ── Component ───────────────────────────────────────────────── */
export function PepeOrb() {
  const orbState         = useUiStore((s) => s.orbState)
  const setOrbState      = useUiStore((s) => s.setOrbState)
  const pushNotification = useUiStore((s) => s.pushNotification)

  /* ── Refs ── */
  const innerRef      = useRef<HTMLDivElement>(null)
  const wsRef         = useRef<WebSocket | null>(null)
  const mediaRef      = useRef<MediaRecorder | null>(null)
  const chunksRef     = useRef<Blob[]>([])
  const timerRef      = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wakeActiveRef = useRef<boolean>(false)  // controlla il loop di wake word

  /* ── Particle sphere — generata una sola volta ── */
  useEffect(() => {
    if (innerRef.current) generateParticles(innerRef.current)
  }, [])

  /* ── Helpers audio ── */
  const stopMedia = useCallback(() => {
    wakeActiveRef.current = false          // ferma il loop di wake word
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

  /**
   * Loop di registrazioni da 3s per wake word detection.
   *
   * Ogni iterazione avvia un NUOVO MediaRecorder → blob WebM completo e valido
   * (con header EBML) → inviato al server → Whisper trascrive → cerca "jarvis".
   *
   * Questo risolve il problema WebM: i chunk timesliced da un MediaRecorder
   * continuo non sono WebM indipendenti (mancano dell'header dopo il primo),
   * rendendo ffmpeg/Whisper incapace di decodificarli. Sessioni discrete
   * producono sempre file WebM validi e auto-contenuti.
   */
  const startChunkStream = useCallback(async (ws: WebSocket) => {
    wakeActiveRef.current = true

    while (wakeActiveRef.current && ws.readyState === WebSocket.OPEN) {
      let stream: MediaStream | null = null
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const chunks: Blob[] = []
        const mr = new MediaRecorder(stream)
        mediaRef.current = mr

        mr.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data) }

        await new Promise<void>((resolve) => {
          mr.onstop = () => {
            stream?.getTracks().forEach((t) => t.stop())
            const blob = new Blob(chunks, { type: 'audio/webm' })
            if (blob.size > 0 && wakeActiveRef.current && ws.readyState === WebSocket.OPEN) {
              blob.arrayBuffer().then((buf) => {
                if (wakeActiveRef.current && ws.readyState === WebSocket.OPEN) ws.send(buf)
              })
            }
            resolve()
          }
          mr.start()
          setTimeout(() => { if (mr.state === 'recording') mr.stop() }, WAKE_SAMPLE_MS)
        })
      } catch (err) {
        console.warn('[PepeOrb] Microfono non disponibile:', err)
        stream?.getTracks().forEach((t) => t.stop())
        break
      }
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
        // Wake word rilevato → il backend riproduce l'ack via ElevenLabs (bloccante).
        // Il frontend manda subito utterance_ready e apre il mic — il drain loop
        // sul server aspetta che l'audio finisca prima di processare l'utterance.
        stopMedia()          // wakeActiveRef = false, ferma MediaRecorder corrente
        setOrbState('listening')
        ws.send(JSON.stringify({ type: 'utterance_ready' }))
        startUtteranceRecording(ws)

      } else if (msg.type === 'speaking') {
        // Pepe sta parlando dagli speaker del Mac (say in corso sul server).
        // Mostriamo lo stato "speaking" — il backend manderà "done" quando finisce.
        setOrbState('speaking')

      } else if (msg.type === 'clarify') {
        // Pepe ha fatto una domanda e aspetta risposta — rimane in ascolto
        // diretto senza tornare al wake word.
        setOrbState('listening')
        startUtteranceRecording(ws)

      } else if (msg.type === 'post_reply_listen') {
        // Step 6: post-reply window — l'utente può rispondere senza wake word.
        // Il server chiuderà la finestra con "done" se non arriva nulla entro timeout_ms.
        setOrbState('listening')
        startUtteranceRecording(ws)

      } else if (msg.type === 'done') {
        // Risposta completata → torna in ascolto wake word
        returnToWakeword()
        startChunkStream(ws)

      } else if (msg.type === 'response') {
        // Ramo legacy — mantenuto per compatibilità futura
        setOrbState('speaking')
        if (msg.text) {
          const utter   = new SpeechSynthesisUtterance(msg.text)
          utter.lang    = 'it-IT'
          utter.rate    = 0.95
          utter.onend   = () => { returnToWakeword(); startChunkStream(ws) }
          utter.onerror = () => { returnToWakeword(); startChunkStream(ws) }
          window.speechSynthesis.speak(utter)
        } else {
          returnToWakeword()
          startChunkStream(ws)
        }

      } else if (msg.type === 'error' || msg.type === 'warning') {
        console.warn('[PepeOrb] Notifica voice backend:', msg)
        pushNotification({
          type:    msg.type === 'warning' ? 'warning' : 'error',
          message: (msg as { message?: string }).message ?? '',
          detail:  (msg as { detail?: string }).detail  ?? '',
          agent:   (msg as { agent?: string }).agent    ?? 'pepe',
          ts:      (msg as { ts?: string }).ts          ?? new Date().toISOString(),
        })
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
