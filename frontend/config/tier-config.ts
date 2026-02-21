// =============================================================================
// COVERAGE STYLING - Color mapping for LLM confidence scores
// =============================================================================

export const getCoverageBg = (coverage: number): string => {
  if (coverage >= 0.95) return 'bg-emerald'
  if (coverage >= 0.9) return 'bg-cyan'
  if (coverage >= 0.85) return 'bg-amber'
  return 'bg-text-muted'
}
