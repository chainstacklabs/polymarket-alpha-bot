// =============================================================================
// TIER CONFIGURATION - Shared across all portfolio views
// =============================================================================

export interface TierConfig {
  label: string
  shortLabel: string
  color: string
  bg: string
  border: string
  desc: string
  textColor: string
}

export const TIER_CONFIG: Record<number, TierConfig> = {
  1: {
    label: 'Excellent',
    shortLabel: 'T1',
    color: 'text-emerald',
    textColor: '#34d399',
    bg: 'bg-emerald/10',
    border: 'border-emerald/30',
    desc: '95%+ LLM confidence'
  },
  2: {
    label: 'Good',
    shortLabel: 'T2',
    color: 'text-cyan',
    textColor: '#38bdf8',
    bg: 'bg-cyan/10',
    border: 'border-cyan/30',
    desc: '90-95% LLM confidence'
  },
  3: {
    label: 'Fair',
    shortLabel: 'T3',
    color: 'text-amber',
    textColor: '#fbbf24',
    bg: 'bg-amber/10',
    border: 'border-amber/30',
    desc: '85-90% LLM confidence'
  },
  4: {
    label: 'Low',
    shortLabel: 'T4',
    color: 'text-text-muted',
    textColor: '#646b78',
    bg: 'bg-text-muted/5',
    border: 'border-border',
    desc: 'Under 85%'
  },
}

export const getTierConfig = (tier: number): TierConfig => {
  return TIER_CONFIG[tier] || TIER_CONFIG[4]
}

export const getCoverageColor = (coverage: number): string => {
  if (coverage >= 0.95) return 'text-emerald'
  if (coverage >= 0.90) return 'text-cyan'
  if (coverage >= 0.85) return 'text-amber'
  return 'text-text-muted'
}

export const getCoverageBg = (coverage: number): string => {
  if (coverage >= 0.95) return 'bg-emerald'
  if (coverage >= 0.90) return 'bg-cyan'
  if (coverage >= 0.85) return 'bg-amber'
  return 'bg-text-muted'
}
