/**
 * NeuralView — ForceGraph3D memory graph + HUD panels
 *
 * Manages selectedNode state: when a node is clicked the NodeDrawer slides in
 * from the right, CollectionStats and StepFeed become visibility:hidden
 * (layout preserved, live data kept alive).
 */

import { useState, useCallback } from 'react'
import { motion }               from 'framer-motion'
import { PepeOrb }              from '../components/PepeOrb/PepeOrb'
import { NeuralBrainOrb }       from '../components/NeuralBrainOrb/NeuralBrainOrb'
import { NodeDrawer }           from '../components/NeuralBrainOrb/NodeDrawer'
import { MemoryStreams }        from '../components/hud/MemoryStreams'
import { CollectionStats }      from '../components/hud/CollectionStats'
import { BridgeActivity }       from '../components/hud/BridgeActivity'
import { StepFeed }             from '../components/hud/StepFeed'
import { useStore }             from '../store'
import type { GraphNode }       from '../components/NeuralBrainOrb/NodeDrawer'

export function NeuralView() {
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)

  const memoryGraph = useStore(s => s.memoryGraph)

  const handleNodeClick = useCallback((node: GraphNode) => {
    setSelectedNode(node)
  }, [])

  const handleClose = useCallback(() => {
    setSelectedNode(null)
  }, [])

  const drawerOpen = selectedNode !== null

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden' }}>

      {/* PepeOrb: voice handler — DOM hidden */}
      <div style={{ display: 'none' }} aria-hidden="true">
        <PepeOrb />
      </div>

      {/* ForceGraph3D canvas — fills entire view */}
      <NeuralBrainOrb
        onNodeClick={handleNodeClick}
        onBackgroundClick={handleClose}
        selectedNodeId={selectedNode?.id ?? null}
      />

      {/* ── HUD panels — absolute overlay ── */}
      <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 10 }}>

        {/* 3.1 — MemoryStreams — top-left, delay 0ms */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 280, damping: 30, delay: 0 }}
          style={{ pointerEvents: 'auto' }}
        >
          <MemoryStreams />
        </motion.div>

        {/* 3.2 — CollectionStats — top-right, hidden when NodeDrawer open */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 280, damping: 30, delay: 0.08 }}
          style={{ pointerEvents: drawerOpen ? 'none' : 'auto', visibility: drawerOpen ? 'hidden' : 'visible' }}
        >
          <CollectionStats />
        </motion.div>

        {/* 3.3 — BridgeActivity — bottom-left, delay 120ms */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 280, damping: 30, delay: 0.12 }}
          style={{ pointerEvents: 'auto' }}
        >
          <BridgeActivity />
        </motion.div>

        {/* 3.4 — StepFeed — bottom-right, hidden when NodeDrawer open */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 280, damping: 30, delay: 0.16 }}
          style={{ pointerEvents: drawerOpen ? 'none' : 'auto', visibility: drawerOpen ? 'hidden' : 'visible' }}
        >
          <StepFeed />
        </motion.div>

      </div>

      {/* ── NodeDrawer — slide-in from right ── */}
      <NodeDrawer
        nodeId={selectedNode?.id ?? null}
        nodes={memoryGraph.nodes}
        edges={memoryGraph.edges}
        onClose={handleClose}
      />

    </div>
  )
}
