/**
 * EtsyView — vista completa pipeline Etsy
 *
 * Layout: two-column (mockup Stitch aligned)
 *   LEFT  (1fr):  ProductionPipeline · BudgetGauges · NicheTable
 *   RIGHT (300px): BundleStatus · AdsStatus · ShopOptimizerCard (sticky)
 *
 * Mobile (< 800px): single-column collapse.
 */

import { motion }              from 'framer-motion'
import { ProductionPipeline }  from '../components/etsy/ProductionPipeline'
import { BudgetGauges }        from '../components/etsy/BudgetGauges'
import { NicheTable }          from '../components/etsy/NicheTable'
import { BundleStatus }        from '../components/etsy/BundleStatus'
import { AdsStatus }           from '../components/etsy/AdsStatus'
import { ShopOptimizerCard }   from '../components/etsy/ShopOptimizerCard'

const springEntry = (delay: number) => ({
  initial:    { opacity: 0, y: 14 } as const,
  animate:    { opacity: 1, y: 0  } as const,
  transition: { type: 'spring' as const, stiffness: 280, damping: 30, delay },
})

export function EtsyView() {
  return (
    <>
      <style>{`
        .etsy-grid {
          display: grid;
          grid-template-columns: 1fr 300px;
          gap: 16px;
          align-items: start;
          width: 100%;
        }
        .etsy-sidebar {
          position: sticky;
          top: 0;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        @media (max-width: 800px) {
          .etsy-grid { grid-template-columns: 1fr; }
          .etsy-sidebar { position: static; }
        }
      `}</style>

      <div style={{
        width:     '100%',
        height:    '100%',
        padding:   '20px',
        overflowY: 'auto',
        overflowX: 'hidden',
        boxSizing: 'border-box',
      }}>
        <div className="etsy-grid">

          {/* ── LEFT: main content ──────────────────────────────────────── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
            <motion.div {...springEntry(0)}>
              <ProductionPipeline />
            </motion.div>
            <motion.div {...springEntry(0.06)}>
              <BudgetGauges />
            </motion.div>
            <motion.div {...springEntry(0.12)}>
              <NicheTable />
            </motion.div>
          </div>

          {/* ── RIGHT: sidebar ──────────────────────────────────────────── */}
          <div className="etsy-sidebar">
            <motion.div {...springEntry(0.03)}>
              <BundleStatus />
            </motion.div>
            <motion.div {...springEntry(0.09)}>
              <AdsStatus />
            </motion.div>
            <motion.div {...springEntry(0.15)}>
              <ShopOptimizerCard />
            </motion.div>
          </div>

        </div>
      </div>
    </>
  )
}
