import { useState, useRef, useCallback, useEffect } from 'react'

// ─── Types ────────────────────────────────────────────────────────
type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking'

// ─── Canvas dimensions ────────────────────────────────────────────
const CW = 580
const CH = 440
const CX = CW / 2
const CY = CH / 2

// ─── State intensity targets ──────────────────────────────────────
const TARGET_INTENSITY: Record<OrbState, number> = {
  idle: 0.50, listening: 0.84, thinking: 1.0, speaking: 0.70,
}

const LABEL: Record<OrbState, string> = {
  idle: '', listening: 'In ascolto…', thinking: 'Elaboro…', speaking: '',
}

// ─── Ring config ─────────────────────────────────────────────────
type RingCfg = {
  rx: number; ry: number; rot: number; speed: number
  lines: number; alpha: number
  beads: { n: number; r: number; s: number } | null
}

const RINGS: RingCfg[] = [
  // Main equatorial (flat, Saturn-like)
  { rx: 238, ry: 50,  rot:  0.04, speed:  0.19, lines: 9, alpha: 0.92, beads: { n: 30, r: 3.6, s:  0.19 } },
  // Tilted A — beads
  { rx: 208, ry: 92,  rot:  0.62, speed: -0.14, lines: 6, alpha: 0.72, beads: { n: 22, r: 2.6, s: -0.14 } },
  // Tilted B — no beads
  { rx: 256, ry: 116, rot: -0.38, speed:  0.10, lines: 5, alpha: 0.54, beads: null },
  // Near-vertical — beads
  { rx: 182, ry: 66,  rot:  1.12, speed: -0.23, lines: 4, alpha: 0.62, beads: { n: 18, r: 2.1, s: -0.23 } },
  // Wide outer — no beads
  { rx: 270, ry: 76,  rot:  0.22, speed:  0.07, lines: 4, alpha: 0.36, beads: null },
]

// ─── Blob path (morphing organic shape) ──────────────────────────
function buildBlob(
  ctx: CanvasRenderingContext2D,
  t: number,
  baseR: number,
  amp: number,
) {
  const N = 120
  ctx.beginPath()
  for (let i = 0; i <= N; i++) {
    const a = (i / N) * Math.PI * 2
    const r = baseR
      + baseR * amp * 0.13 * Math.sin(2 * a + t * 0.68)
      + baseR * amp * 0.09 * Math.sin(3 * a - t * 0.91)
      + baseR * amp * 0.06 * Math.sin(5 * a + t * 1.15)
      + baseR * amp * 0.04 * Math.sin(4 * a - t * 0.57)
    const x = CX + r * Math.cos(a)
    const y = CY + r * Math.sin(a)
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
  }
  ctx.closePath()
}

// ─── Draw rings + beads ───────────────────────────────────────────
function drawRings(ctx: CanvasRenderingContext2D, t: number, intensity: number) {
  for (const ring of RINGS) {
    const ia = ring.alpha * intensity

    // Stacked ellipses → neon light-trail bundle (no shadow per line, one glow pass)
    for (let li = 0; li < ring.lines; li++) {
      const ratio = li / (ring.lines - 1)
      const ryV   = ring.ry + (li - ring.lines / 2) * 1.6
      const a     = ia * (0.06 + ratio * 0.34)
      ctx.beginPath()
      ctx.ellipse(CX, CY, ring.rx, ryV, ring.rot, 0, Math.PI * 2)
      ctx.strokeStyle = `rgba(45,232,106,${a.toFixed(3)})`
      ctx.lineWidth   = 0.9
      ctx.stroke()
    }

    // Single glow pass per ring (cheaper than per-line shadow)
    ctx.save()
    ctx.shadowBlur  = 18
    ctx.shadowColor = `rgba(45,232,106,${(ia * 0.55).toFixed(3)})`
    ctx.beginPath()
    ctx.ellipse(CX, CY, ring.rx, ring.ry, ring.rot, 0, Math.PI * 2)
    ctx.strokeStyle = `rgba(45,232,106,${(ia * 0.38).toFixed(3)})`
    ctx.lineWidth   = 1.0
    ctx.stroke()
    ctx.restore()

    // Bead chain
    if (!ring.beads) continue
    const { n, r: br, s: bs } = ring.beads
    const cosR = Math.cos(ring.rot)
    const sinR = Math.sin(ring.rot)

    for (let b = 0; b < n; b++) {
      const angle = (b / n) * Math.PI * 2 + t * bs
      const ex = ring.rx * Math.cos(angle)
      const ey = ring.ry * Math.sin(angle)
      const bx = CX + ex * cosR - ey * sinR
      const by = CY + ex * sinR + ey * cosR

      // Depth cue: beads "behind" are dimmer
      const depth = 0.28 + 0.72 * (0.5 + 0.5 * Math.sin(angle))
      const ba    = ia * depth

      ctx.save()
      ctx.shadowBlur  = 9
      ctx.shadowColor = `rgba(45,232,106,${ba.toFixed(2)})`
      ctx.beginPath()
      ctx.arc(bx, by, br, 0, Math.PI * 2)
      // Specular highlight bead
      const bg = ctx.createRadialGradient(bx - br * 0.3, by - br * 0.35, 0, bx, by, br)
      bg.addColorStop(0,   `rgba(255,255,255,${ba.toFixed(2)})`)
      bg.addColorStop(0.4, `rgba(160,255,190,${(ba * 0.78).toFixed(2)})`)
      bg.addColorStop(1,   `rgba(45,232,106,${(ba * 0.28).toFixed(2)})`)
      ctx.fillStyle = bg
      ctx.fill()
      ctx.restore()
    }
  }
}

// ─── Draw blob (morphing core + internal rays) ────────────────────
function drawBlob(
  ctx: CanvasRenderingContext2D,
  t: number,
  intensity: number,
  state: OrbState,
) {
  const breathing = 1 + 0.022 * Math.sin(t * 0.85)
  const baseR     = 90 * breathing
  const morphAmp  = state === 'thinking' ? 1.9 : state === 'listening' ? 1.5 : 1.0

  // ── Outer halo glow ──
  buildBlob(ctx, t, baseR * 1.18, morphAmp)
  ctx.save()
  ctx.shadowBlur  = 40
  ctx.shadowColor = `rgba(45,232,106,${(0.55 * intensity).toFixed(3)})`
  ctx.fillStyle   = `rgba(12,55,28,${(0.015).toFixed(3)})`
  ctx.fill()
  ctx.restore()

  // ── Main blob fill (dark rim → bright glowing center) ──
  buildBlob(ctx, t, baseR, morphAmp)
  const fill = ctx.createRadialGradient(
    CX - baseR * 0.22, CY - baseR * 0.26, 0,
    CX, CY, baseR * 1.06,
  )
  fill.addColorStop(0,    `rgba(255,255,255,${(0.96 * intensity).toFixed(3)})`)
  fill.addColorStop(0.11, `rgba(195,255,212,${(0.84 * intensity).toFixed(3)})`)
  fill.addColorStop(0.30, `rgba(45,185,100,${(0.58 * intensity).toFixed(3)})`)
  fill.addColorStop(0.62, `rgba(7,48,22,${(0.75 * intensity).toFixed(3)})`)
  fill.addColorStop(1,    'rgba(2,8,4,0.97)')
  ctx.save()
  ctx.shadowBlur  = 22
  ctx.shadowColor = `rgba(45,232,106,${(0.38 * intensity).toFixed(3)})`
  ctx.fillStyle   = fill
  ctx.fill()
  ctx.restore()

  // ── Clip blob → draw internal light rays ──
  buildBlob(ctx, t, baseR, morphAmp)
  ctx.save()
  ctx.clip()

  // Additive blending: overlapping rays create bright white core naturally
  ctx.globalCompositeOperation = 'lighter'

  const rayN = 42
  for (let i = 0; i < rayN; i++) {
    const angle   = (i / rayN) * Math.PI * 2 + t * 0.030
    const len     = baseR * (0.50 + 0.50 * Math.abs(Math.sin(t * 1.03 + i * 0.43)))
    const opacity = (0.045 + 0.055 * Math.abs(Math.sin(t * 0.80 + i * 0.57))) * intensity
    const lw      = 0.5 + 0.55 * Math.abs(Math.sin(t * 0.62 + i * 0.9))

    ctx.beginPath()
    ctx.moveTo(CX, CY)
    ctx.lineTo(CX + len * Math.cos(angle), CY + len * Math.sin(angle))
    ctx.strokeStyle = `rgba(160,255,190,${opacity.toFixed(3)})`
    ctx.lineWidth   = lw
    ctx.stroke()
  }

  // Core bright spot (additive, creates the white burst)
  const coreG = ctx.createRadialGradient(CX, CY, 0, CX, CY, baseR * 0.45)
  coreG.addColorStop(0,    `rgba(255,255,255,${(0.75 * intensity).toFixed(3)})`)
  coreG.addColorStop(0.2,  `rgba(200,255,220,${(0.45 * intensity).toFixed(3)})`)
  coreG.addColorStop(0.55, `rgba(45,232,106,${(0.15 * intensity).toFixed(3)})`)
  coreG.addColorStop(1,    'rgba(0,0,0,0)')
  ctx.fillStyle = coreG
  ctx.fillRect(CX - baseR, CY - baseR, baseR * 2, baseR * 2)

  ctx.globalCompositeOperation = 'source-over'
  ctx.restore()

  // ── Blob rim (neon outline) ──
  buildBlob(ctx, t, baseR, morphAmp)
  ctx.save()
  ctx.shadowBlur  = 16
  ctx.shadowColor = `rgba(45,232,106,${(0.65 * intensity).toFixed(3)})`
  ctx.strokeStyle = `rgba(45,232,106,${(0.52 * intensity).toFixed(3)})`
  ctx.lineWidth   = 1.4
  ctx.stroke()
  ctx.restore()
}

// ─── Web Speech API ───────────────────────────────────────────────
const SpeechRecognitionAPI: any =
  typeof window !== 'undefined'
    ? (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    : null

// ─── Component ────────────────────────────────────────────────────
export function PepeOrb() {
  const [state, setState]         = useState<OrbState>('idle')
  const [lastQuery, setLastQuery] = useState('')
  const [error, setError]         = useState('')
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const recRef    = useRef<any>(null)
  const stateRef  = useRef<OrbState>('idle')

  useEffect(() => { stateRef.current = state }, [state])

  // ── Animation loop ──────────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    let raf        = 0
    let t          = 0
    let intensity  = TARGET_INTENSITY.idle
    let morphSpeed = 1.0

    function loop() {
      t += 0.010
      const s = stateRef.current

      // Smooth lerp toward target values
      intensity  += (TARGET_INTENSITY[s] - intensity)  * 0.04
      const targetMS = s === 'thinking' ? 2.2 : s === 'listening' ? 1.6 : 1.0
      morphSpeed += (targetMS - morphSpeed) * 0.04

      ctx.clearRect(0, 0, CW, CH)

      // Ambient background glow
      const bgG = ctx.createRadialGradient(CX, CY, 0, CX, CY, 255)
      bgG.addColorStop(0,    `rgba(45,232,106,${(0.062 * intensity).toFixed(3)})`)
      bgG.addColorStop(0.48, `rgba(18,90,42,${(0.028 * intensity).toFixed(3)})`)
      bgG.addColorStop(1,    'rgba(0,0,0,0)')
      ctx.fillStyle = bgG
      ctx.fillRect(0, 0, CW, CH)

      drawRings(ctx, t, intensity)
      drawBlob(ctx, t * morphSpeed, intensity, s)

      raf = requestAnimationFrame(loop)
    }

    loop()
    return () => cancelAnimationFrame(raf)
  }, [])

  // ── Voice result handler ────────────────────────────────────────
  const handleVoiceResult = useCallback(async (text: string) => {
    setLastQuery(text)
    setState('thinking')
    try {
      const res  = await fetch('/api/personal/ask', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ text }),
      })
      const data  = await res.json()
      const reply = data.response || data.error || 'Nessuna risposta.'
      setState('speaking')
      const utter     = new SpeechSynthesisUtterance(reply)
      utter.lang      = 'it-IT'
      utter.rate      = 0.95
      utter.onend     = () => setState('idle')
      utter.onerror   = () => setState('idle')
      window.speechSynthesis.speak(utter)
    } catch {
      setState('idle')
      setError('Errore connessione')
      setTimeout(() => setError(''), 3000)
    }
  }, [])

  // ── Start listening ─────────────────────────────────────────────
  const startListening = useCallback(() => {
    if (!SpeechRecognitionAPI) {
      setError('Voce non supportata — usa Chrome')
      setTimeout(() => setError(''), 3500)
      return
    }
    setState('listening')
    const rec           = new SpeechRecognitionAPI()
    rec.lang            = 'it-IT'
    rec.continuous      = false
    rec.interimResults  = false
    rec.onresult = (e: any) => {
      const txt: string = e.results[0]?.[0]?.transcript?.trim() ?? ''
      if (txt) handleVoiceResult(txt)
      else setState('idle')
    }
    rec.onerror = () => setState('idle')
    rec.onend   = () => { if (stateRef.current === 'listening') setState('idle') }
    rec.start()
    recRef.current = rec
  }, [handleVoiceResult])

  // ── Click ───────────────────────────────────────────────────────
  const handleClick = useCallback(() => {
    if      (state === 'idle')      startListening()
    else if (state === 'listening') { recRef.current?.stop(); setState('idle') }
    else if (state === 'speaking')  { window.speechSynthesis.cancel(); setState('idle') }
  }, [state, startListening])

  // ── Cleanup ─────────────────────────────────────────────────────
  useEffect(() => () => {
    recRef.current?.stop()
    window.speechSynthesis.cancel()
  }, [])

  const label = LABEL[state]

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      position: 'relative',
      overflow: 'hidden',
      background: 'var(--base)',
      userSelect: 'none',
    }}>

      <canvas
        ref={canvasRef}
        width={CW}
        height={CH}
        onClick={handleClick}
        title={
          state === 'idle'      ? 'Clicca per parlare' :
          state === 'listening' ? 'Clicca per fermare'  :
          state === 'speaking'  ? 'Clicca per fermare'  : undefined
        }
        style={{
          display: 'block',
          cursor:  state === 'thinking' ? 'wait' : 'pointer',
          animation: 'orb-entry 0.9s var(--e-spring) both',
        }}
      />

      {/* Status label */}
      <div style={{
        marginTop: -6,
        height: 18,
        fontFamily: 'var(--fd)',
        fontSize: 11,
        letterSpacing: '0.09em',
        textTransform: 'uppercase',
        color: 'var(--accent)',
        opacity: label ? 1 : 0,
        transition: 'opacity 0.3s var(--e-io)',
        pointerEvents: 'none',
      }}>
        {label}
      </div>

      {/* Error */}
      {error && (
        <div style={{
          marginTop: 4,
          fontFamily: 'var(--fd)',
          fontSize: 10,
          letterSpacing: '0.06em',
          color: 'var(--err)',
          animation: 'fadeSlideUp 0.25s var(--e-out) both',
        }}>
          {error}
        </div>
      )}

      {/* Last query */}
      {lastQuery && state === 'idle' && !error && (
        <div style={{
          marginTop: 4,
          fontFamily: 'var(--fb)',
          fontSize: 12,
          color: 'var(--tm)',
          maxWidth: '60%',
          textAlign: 'center',
          opacity: 0.65,
          animation: 'fadeSlideUp 0.3s var(--e-out) both',
          pointerEvents: 'none',
        }}>
          «{lastQuery}»
        </div>
      )}

      {/* No voice support notice */}
      {!SpeechRecognitionAPI && (
        <div style={{
          position: 'absolute',
          bottom: 12,
          fontFamily: 'var(--fd)',
          fontSize: 9,
          letterSpacing: '0.07em',
          color: 'var(--tf)',
        }}>
          VOICE · CHROME / EDGE ONLY
        </div>
      )}
    </div>
  )
}
