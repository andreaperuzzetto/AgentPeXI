export const springEntry = (delay: number) => ({
  initial:    { opacity: 0, y: 14 } as const,
  animate:    { opacity: 1, y: 0  } as const,
  transition: { type: 'spring' as const, stiffness: 280, damping: 30, delay },
})

export const SIDEBAR_PANELS: { name: string; delay: number }[] = [
  { name: 'ShopIdentityPanel', delay: 0    },
  { name: 'SectionsPanel',     delay: 0.03 },
  { name: 'BundleStatus',      delay: 0.06 },
  { name: 'AdsStatus',         delay: 0.09 },
  { name: 'PinterestPanel',    delay: 0.12 },
  { name: 'ShopOptimizerCard', delay: 0.15 },
]
