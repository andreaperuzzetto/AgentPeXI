import { useRef, useEffect } from 'react'
import { useUiStore, type OrbState } from '../../store/uiStore'
import './PepeOrb.css'

const LABELS: Record<OrbState, string> = {
  wakeword:  '',
  listening: 'listening...',
  thinking:  'thinking...',
  speaking:  'speaking',
}

const STATES: OrbState[] = ['wakeword', 'listening', 'thinking', 'speaking']
const STATE_LABELS: Record<OrbState, string> = {
  wakeword:  'Standby',
  listening: 'Listen',
  thinking:  'Think',
  speaking:  'Speak',
}
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

export function PepeOrb() {
  const orbState    = useUiStore((s) => s.orbState)
  const setOrbState = useUiStore((s) => s.setOrbState)
  const innerRef    = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (innerRef.current) {
      generateParticles(innerRef.current)
    }
  }, [])

  return (
    <>
      {/* atmosphere glow */}
      <div className="orb-atmos" />

      {/* particle sphere */}
      <div className={`orb-wrap s-${orbState}`}>
        <div className="orb-inner" ref={innerRef} />
      </div>

      {/* state label */}
      <div className="orb-state-lbl">{LABELS[orbState]}</div>

      {/* state controls — bottom-left */}
      <div className="orb-controls">
        {STATES.map((s) => (
          <button
            key={s}
            className={`orb-btn${orbState === s ? ' active' : ''}`}
            onClick={() => setOrbState(s)}
          >
            {STATE_LABELS[s]}
          </button>
        ))}
      </div>
    </>
  )
}
