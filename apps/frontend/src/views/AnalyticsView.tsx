/**
 * AnalyticsView — FE-Blocco 5
 *
 * Layout 2 colonne:
 *   LEFT  (1fr):  TokenCostChart · CtrAbPanel
 *   RIGHT (320px): FinancePanel · LadderSummary (sticky)
 *
 * Mobile (< 800px): single-column collapse.
 */

import { motion }          from 'framer-motion'
import { TokenCostChart }  from '../components/analytics/TokenCostChart'
import { FinancePanel }    from '../components/analytics/FinancePanel'
import { CtrAbPanel }      from '../components/analytics/CtrAbPanel'
import { LadderSummary }   from '../components/analytics/LadderSummary'

const springEntry = (delay: number) => ({
  initial:    { opacity: 0, y: 14 } as const,
  animate:    { opacity: 1, y: 0  } as const,
  transition: { type: 'spring' as const, stiffness: 280, damping: 30, delay },
})

export function AnalyticsView() {
  return (
    <>
      <style>{`
        .analytics-grid {
          display: grid;
          grid-template-columns: 1fr 320px;
          gap: 16px;
          align-items: start;
          width: 100%;
        }
        .analytics-sidebar {
          position: sticky;
          top: 0;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        @media (max-width: 800px) {
          .analytics-grid   { grid-template-columns: 1fr; }
          .analytics-sidebar { position: static; }
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
        <div className="analytics-grid">

          {/* ── LEFT: main content ──────────────────────────────────────── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
            <motion.div {...springEntry(0)}>
              <TokenCostChart />
            </motion.div>
            <motion.div {...springEntry(0.08)}>
              <CtrAbPanel />
            </motion.div>
          </div>

          {/* ── RIGHT: sidebar ──────────────────────────────────────────── */}
          <div className="analytics-sidebar">
            <motion.div {...springEntry(0.04)}>
              <FinancePanel />
            </motion.div>
            <motion.div {...springEntry(0.12)}>
              <LadderSummary />
            </motion.div>
          </div>

        </div>
      </div>
    </>
  )
}

