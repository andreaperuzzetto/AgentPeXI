/**
 * NeuralView — full-screen Three.js memory graph + HUD panels
 *
 * FE-2.1: NeuralBrainOrb replaces the old Canvas-2D NeuralBrain.
 * PepeOrb (voice WebSocket handler) is kept as hidden DOM node.
 * StepCards + StepDrawer are kept until FE-3 HUD panels are complete.
 *
 * Layout:
 *   NeuralBrainOrb fills 100% of the view (background + canvas)
 *   HUD panels (FE-3) will be positioned absolute over the canvas
 *   StepDrawer slides up from the bottom edge
 */

import { useState } from 'react'
import { PepeOrb }      from '../components/PepeOrb/PepeOrb'
import { StepCards }    from '../components/OrbOverlay/StepCards'
import { StepDrawer }   from '../components/OrbOverlay/StepDrawer'
import { NeuralBrainOrb } from '../components/NeuralBrainOrb/NeuralBrainOrb'

export function NeuralView() {
  const [drawerOpen, setDrawerOpen] = useState(false)

  return (
    <div
      style={{
        position:  'relative',
        width:     '100%',
        height:    '100%',
        overflow:  'hidden',
      }}
    >
      {/* PepeOrb: voice WebSocket handler — logic runs, DOM hidden */}
      <div style={{ display: 'none' }} aria-hidden="true">
        <PepeOrb />
      </div>

      {/* Three.js memory graph — fills entire view */}
      <NeuralBrainOrb />

      {/* Step cards float above canvas — legacy, FE-3 will supersede */}
      <StepCards hidden={drawerOpen} />

      {/* Step drawer slides up from bottom edge */}
      <StepDrawer
        open={drawerOpen}
        onToggle={() => setDrawerOpen(v => !v)}
      />
    </div>
  )
}
