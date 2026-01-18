'use client'

import { useEffect, useCallback } from 'react'
import { getCoverageColor, getCoverageBg } from '@/config/tier-config'

// =============================================================================
// TYPES
// =============================================================================

export interface Portfolio {
  pair_id: string
  target_group_title: string
  target_group_slug?: string
  target_market_slug?: string
  target_question: string
  target_position: 'YES' | 'NO'
  target_price: number
  target_bracket?: string
  cover_group_title: string
  cover_group_slug?: string
  cover_market_slug?: string
  cover_question: string
  cover_position: 'YES' | 'NO'
  cover_price: number
  cover_bracket?: string
  cover_probability: number
  total_cost: number
  coverage: number
  loss_probability: number
  expected_profit: number
  tier: number
  tier_label: string
  relationship?: string
  validation_analysis?: string
  viability_score?: number
}

interface PortfolioModalProps {
  portfolio: Portfolio
  onClose: () => void
}

// =============================================================================
// MODAL COMPONENT
// =============================================================================

export function PortfolioModal({ portfolio: p, onClose }: PortfolioModalProps) {
  const isProfitable = p.expected_profit > 0.001
  const coverageColor = getCoverageColor(p.coverage)
  const coverageBg = getCoverageBg(p.coverage)

  // ESC key handler
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
  }, [onClose])

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = ''
    }
  }, [handleKeyDown])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      {/* Backdrop with blur */}
      <div className="absolute inset-0 bg-void/80 backdrop-blur-sm" />

      {/* Modal */}
      <div
        className="relative w-full max-w-xl bg-surface border border-border rounded-xl shadow-2xl overflow-hidden animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Top accent line */}
        <div className={`h-0.5 ${coverageBg}`} />

        {/* Close button */}
        <div className="absolute top-3 right-3">
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-surface-elevated transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="px-5 py-5 space-y-4">
          {/* Bet Flow Visualization */}
          <div className="space-y-3">
            {/* Target Bet */}
            <div className="group relative bg-surface-elevated rounded-lg p-3.5 border border-border hover:border-border-glow transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-[10px] font-medium text-text-muted uppercase tracking-wide">Main Bet</span>
                    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${p.target_position === 'YES' ? 'bg-emerald/15 text-emerald' : 'bg-rose/15 text-rose'}`}>
                      {p.target_position} @ ${p.target_price.toFixed(2)}
                    </span>
                  </div>
                  <p className="text-sm text-text-primary leading-snug">{p.target_question}</p>
                  <p className="text-[11px] text-text-muted mt-1">{p.target_group_title}</p>
                  {p.target_bracket && (
                    <span className="inline-block mt-1 text-[10px] text-text-muted bg-surface px-1.5 py-0.5 rounded border border-border">
                      {p.target_bracket}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Flow Connection */}
            <div className="flex items-center justify-center py-0.5">
              <div className="flex items-center gap-2 text-text-muted">
                <div className="w-6 h-px bg-border" />
                <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface-elevated border border-border">
                  <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                  <span className="text-[10px] font-medium tracking-wide">
                    {(p.cover_probability * 100).toFixed(0)}% trigger
                  </span>
                </div>
                <div className="w-6 h-px bg-border" />
              </div>
            </div>

            {/* Backup Bet */}
            <div className="group relative bg-surface-elevated rounded-lg p-3.5 border-2 border-cyan/20 hover:border-cyan/40 transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-[10px] font-medium text-cyan uppercase tracking-wide">Backup Bet</span>
                    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${p.cover_position === 'YES' ? 'bg-emerald/15 text-emerald' : 'bg-rose/15 text-rose'}`}>
                      {p.cover_position} @ ${p.cover_price.toFixed(2)}
                    </span>
                  </div>
                  <p className="text-sm text-text-primary leading-snug">{p.cover_question}</p>
                  <p className="text-[11px] text-text-muted mt-1">{p.cover_group_title}</p>
                  {p.cover_bracket && (
                    <span className="inline-block mt-1 text-[10px] text-text-muted bg-surface px-1.5 py-0.5 rounded border border-border">
                      {p.cover_bracket}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* AI Analysis */}
          {p.validation_analysis && (
            <div className="bg-surface-elevated/50 rounded-lg p-3.5 border border-border">
              <div className="flex items-center gap-2 mb-1">
                <h4 className="text-[10px] font-medium text-violet-400 uppercase tracking-wide">AI Analysis</h4>
                {p.viability_score !== undefined && (
                  <span className="text-[10px] font-mono text-text-muted">
                    {(p.viability_score * 100).toFixed(0)}% confidence
                  </span>
                )}
              </div>
              <p className="text-xs text-text-secondary leading-relaxed">{p.validation_analysis}</p>
            </div>
          )}

          {/* Key Metrics - Compact row */}
          <div className="grid grid-cols-3 gap-2">
            <div className="bg-surface-elevated rounded-lg p-2.5 text-center group/conf relative">
              <p className={`text-base font-mono font-semibold ${coverageColor}`}>
                {(p.coverage * 100).toFixed(1)}%
              </p>
              <p className="text-[9px] text-text-muted uppercase tracking-wide mt-0.5 flex items-center justify-center gap-0.5">
                LLM Conf.
                <span className="w-3 h-3 rounded-full bg-surface border border-border text-[7px] flex items-center justify-center opacity-50 group-hover/conf:opacity-100 transition-opacity cursor-help">?</span>
              </p>
              {/* Tooltip */}
              <div className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 w-64 p-2 bg-surface border border-border rounded-lg shadow-lg opacity-0 invisible group-hover/conf:opacity-100 group-hover/conf:visible transition-all z-50 text-left pointer-events-none">
                <p className="text-[10px] font-medium text-violet-400 mb-1">LLM Confidence</p>
                <p className="text-[9px] text-text-secondary mb-1.5">Probability of $1 payout based on LLM-detected logical relationships.</p>
                <p className="text-[9px] text-text-muted">Formula: P(target) + P(¬target) × P(cover)</p>
              </div>
            </div>
            <div className="bg-surface-elevated rounded-lg p-2.5 text-center">
              <p className="text-base font-mono font-semibold text-text-primary">
                ${p.total_cost.toFixed(2)}
              </p>
              <p className="text-[9px] text-text-muted uppercase tracking-wide mt-0.5">Cost</p>
            </div>
            <div className="bg-surface-elevated rounded-lg p-2.5 text-center">
              <p className={`text-base font-mono font-semibold ${isProfitable ? 'text-emerald' : 'text-rose'}`}>
                {isProfitable ? '+' : ''}{(p.expected_profit * 100).toFixed(1)}%
              </p>
              <p className="text-[9px] text-text-muted uppercase tracking-wide mt-0.5">Return</p>
            </div>
          </div>

          {/* Action Button */}
          {(p.target_group_slug || p.cover_group_slug) && (
            <button
              onClick={() => {
                if (p.target_group_slug) {
                  window.open(`https://polymarket.com/event/${p.target_group_slug}`, '_blank')
                }
                if (p.cover_group_slug) {
                  window.open(`https://polymarket.com/event/${p.cover_group_slug}`, '_blank')
                }
              }}
              className="w-full py-2.5 px-4 bg-cyan/10 hover:bg-cyan/15 border border-cyan/25 hover:border-cyan/40 rounded-lg text-cyan text-sm font-medium transition-all flex items-center justify-center gap-2"
            >
              <span>Open Markets on Polymarket</span>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
