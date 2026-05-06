/**
 * EtsyView — vista completa pipeline Etsy
 *
 * Layout: two-column
 *   CENTER (1fr):   ProductionPipeline · BudgetGauges · NicheTable
 *   RIGHT  (320px): ShopIdentityPanel · SectionsPanel · BundleStatus · AdsStatus · PinterestPanel · ShopOptimizerCard (sticky)
 *
 * Mobile (< 900px): single-column collapse.
 */

import { motion }              from 'framer-motion'
import { ProductionPipeline }  from '../components/etsy/ProductionPipeline'
import { BudgetGauges }        from '../components/etsy/BudgetGauges'
import { NicheTable }          from '../components/etsy/NicheTable'
import { BundleStatus }        from '../components/etsy/BundleStatus'
import { AdsStatus }           from '../components/etsy/AdsStatus'
import { PinterestPanel }      from '../components/etsy/PinterestPanel'
import { ShopOptimizerCard }   from '../components/etsy/ShopOptimizerCard'
import { SectionsPanel }       from '../components/etsy/SectionsPanel'
import { ShopIdentityPanel }   from '../components/etsy/ShopIdentityPanel'
import { springEntry }          from './EtsyView.helpers'

export function EtsyView() {
  return (
    <>
      <style>{`
        .etsy-grid {
          display: grid;
          grid-template-columns: 1fr 320px;
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
        @media (max-width: 900px) {
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

          {/* ── CENTER: main content ────────────────────────────────────── */}
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
            <motion.div {...springEntry(0)}>
              <ShopIdentityPanel />
            </motion.div>
            <motion.div {...springEntry(0.03)}>
              <SectionsPanel />
            </motion.div>
            <motion.div {...springEntry(0.06)}>
              <BundleStatus />
            </motion.div>
            <motion.div {...springEntry(0.09)}>
              <AdsStatus />
            </motion.div>
            <motion.div {...springEntry(0.12)}>
              <PinterestPanel />
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
