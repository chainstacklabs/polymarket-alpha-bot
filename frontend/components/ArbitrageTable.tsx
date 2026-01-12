'use client'

import { useState } from 'react'

// =============================================================================
// TYPES
// =============================================================================

export interface ArbitragePosition {
  event_id: string
  title: string
  slug: string | null
  position: 'YES' | 'NO'
  price: number
  price_display: string
  outcome_covered: string
  market_url: string
}

export interface ArbitrageOpportunity {
  signal_id: string
  opportunity_type: 'arbitrage'
  positions: ArbitragePosition[]
  total_cost: number
  total_cost_display: string
  profit: number
  profit_display: string
  num_markets: number
  confidence: number
  confidence_adjusted_profit: number
  reasoning: string
  strategy: {
    description: string
  }
}

interface ArbitrageTableProps {
  opportunities: ArbitrageOpportunity[]
  loading?: boolean
  onSelect?: (opportunity: ArbitrageOpportunity) => void
}

// =============================================================================
// COMPONENT
// =============================================================================

export function ArbitrageTable({ opportunities, loading, onSelect }: ArbitrageTableProps) {
  // Only use internal state if no onSelect callback provided (backwards compatibility)
  const [internalSelected, setInternalSelected] = useState<ArbitrageOpportunity | null>(null)
  const [filter, setFilter] = useState('')

  const handleSelect = (opp: ArbitrageOpportunity) => {
    if (onSelect) {
      onSelect(opp)
    } else {
      setInternalSelected(opp)
    }
  }

  const filteredOpportunities = opportunities.filter(opp => {
    if (!filter) return true
    const search = filter.toLowerCase()
    return opp.positions.some(p =>
      p.title.toLowerCase().includes(search) ||
      p.outcome_covered.toLowerCase().includes(search)
    )
  })

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <span className="text-sm text-text-muted">Loading arbitrage opportunities...</span>
      </div>
    )
  }

  if (opportunities.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <span className="text-sm text-text-muted">No arbitrage opportunities found</span>
        <p className="text-xs text-text-muted mt-2 max-w-md">
          Cross-market arbitrage requires semantically similar events from different markets
          where the sum of prices for covering all outcomes differs from 100%.
        </p>
      </div>
    )
  }

  return (
    <>
      <div className="space-y-4">
        {/* Filter */}
        <div className="flex items-center justify-between">
          <span className="text-sm text-text-muted">
            {filteredOpportunities.length} arbitrage opportunities
          </span>
          <input
            type="text"
            placeholder="Filter by market..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="px-3 py-1.5 w-48 bg-surface-elevated border border-border rounded text-sm text-text-primary placeholder:text-text-muted focus:border-cyan/50 focus:outline-none transition-colors"
          />
        </div>

        {/* Opportunities Grid */}
        <div className="grid gap-4 md:grid-cols-2">
          {filteredOpportunities.map((opp) => (
            <ArbitrageCard
              key={opp.signal_id}
              opportunity={opp}
              onClick={() => handleSelect(opp)}
            />
          ))}
        </div>
      </div>

      {/* Detail Modal - only render internally if no onSelect callback */}
      {!onSelect && internalSelected && (
        <ArbitrageDetailModal
          opportunity={internalSelected}
          onClose={() => setInternalSelected(null)}
        />
      )}
    </>
  )
}

// =============================================================================
// ARBITRAGE CARD
// =============================================================================

function ArbitrageCard({
  opportunity,
  onClick,
}: {
  opportunity: ArbitrageOpportunity
  onClick: () => void
}) {
  const profitPercent = Math.round(opportunity.profit * 100)
  const totalCostPercent = Math.round(opportunity.total_cost * 100)

  return (
    <div
      className="bg-surface border border-border rounded-lg p-4 border-l-2 border-l-emerald transition-colors hover:bg-surface-hover cursor-pointer"
      onClick={onClick}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-text-muted">
            {opportunity.signal_id}
          </span>
          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted border border-border">
            {opportunity.num_markets} markets
          </span>
        </div>
        <span className="text-sm font-semibold font-mono text-emerald">
          +{profitPercent}% profit
        </span>
      </div>

      {/* Positions Summary */}
      <div className="space-y-1.5 mb-3">
        {opportunity.positions.slice(0, 3).map((position, idx) => (
          <div key={idx} className="flex items-center gap-2">
            <span
              className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                position.position === 'YES'
                  ? 'bg-emerald/10 text-emerald'
                  : 'bg-amber/10 text-amber'
              }`}
            >
              {position.position}
            </span>
            <span className="text-sm text-text-primary truncate flex-1" title={position.title}>
              {position.title}
            </span>
            <span className="text-xs font-mono text-text-muted">
              {position.price_display}
            </span>
          </div>
        ))}
        {opportunity.positions.length > 3 && (
          <span className="text-xs text-text-muted">
            +{opportunity.positions.length - 3} more markets
          </span>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between pt-3 border-t border-border">
        <div className="flex items-center gap-3">
          <div className="text-xs text-text-muted">
            Sum: <span className="font-mono">{totalCostPercent}%</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="w-10 h-1 bg-surface-elevated rounded-full overflow-hidden">
              <div
                className="h-full bg-cyan rounded-full"
                style={{ width: `${opportunity.confidence * 100}%` }}
              />
            </div>
            <span className="text-[10px] font-mono text-text-muted">
              {Math.round(opportunity.confidence * 100)}%
            </span>
          </div>
        </div>
        <span className="text-xs text-text-secondary">View details →</span>
      </div>
    </div>
  )
}

// =============================================================================
// DETAIL MODAL
// =============================================================================

export function ArbitrageDetailModal({
  opportunity,
  onClose,
}: {
  opportunity: ArbitrageOpportunity
  onClose: () => void
}) {
  const profitPercent = (opportunity.profit * 100).toFixed(1)
  const totalCostPercent = (opportunity.total_cost * 100).toFixed(1)

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-surface border border-border rounded-lg max-w-2xl w-full max-h-[90vh] overflow-y-auto animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div className="flex items-center gap-3">
            <span className="text-sm font-mono text-text-muted">{opportunity.signal_id}</span>
            <span className="text-sm font-semibold text-emerald">
              +{profitPercent}% Potential Profit
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary transition-colors text-xl"
          >
            ×
          </button>
        </div>

        {/* Modal Content */}
        <div className="p-4 space-y-4">
          {/* Strategy Overview */}
          <div className="bg-emerald/5 border border-emerald/20 rounded-lg p-4">
            <h4 className="text-[10px] font-medium text-emerald uppercase tracking-wider mb-2">
              Arbitrage Strategy
            </h4>
            <p className="text-sm text-text-primary">
              Buy the following {opportunity.num_markets} positions to cover all possible outcomes.
              Exactly one will pay out $1.00.
            </p>
          </div>

          {/* Positions Table */}
          <div className="bg-surface-elevated rounded-lg border border-border overflow-hidden">
            <table className="w-full">
              <thead className="bg-surface border-b border-border">
                <tr>
                  <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted">
                    Position
                  </th>
                  <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted">
                    Market
                  </th>
                  <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted">
                    Outcome Covered
                  </th>
                  <th className="px-3 py-2 text-right text-[10px] font-medium uppercase tracking-wider text-text-muted">
                    Price
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {opportunity.positions.map((position, idx) => (
                  <tr key={idx} className="hover:bg-surface-hover transition-colors">
                    <td className="px-3 py-2">
                      <span
                        className={`text-xs font-mono px-2 py-0.5 rounded ${
                          position.position === 'YES'
                            ? 'bg-emerald/10 text-emerald'
                            : 'bg-amber/10 text-amber'
                        }`}
                      >
                        {position.position}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <a
                        href={position.market_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm text-text-primary hover:text-cyan transition-colors"
                        title={position.title}
                      >
                        {position.title.length > 50
                          ? position.title.slice(0, 50) + '...'
                          : position.title}
                      </a>
                    </td>
                    <td className="px-3 py-2">
                      <span className="text-xs text-text-secondary">
                        {position.outcome_covered}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="text-sm font-mono text-text-primary">
                        ${position.price.toFixed(2)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-surface border-t border-border">
                <tr>
                  <td colSpan={3} className="px-3 py-2 text-sm text-text-muted text-right">
                    Total Cost
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className="text-sm font-mono font-semibold text-text-primary">
                      ${opportunity.total_cost.toFixed(2)}
                    </span>
                  </td>
                </tr>
                <tr className="bg-emerald/5">
                  <td colSpan={3} className="px-3 py-2 text-sm text-emerald text-right font-medium">
                    Potential Profit
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className="text-sm font-mono font-semibold text-emerald">
                      +${opportunity.profit.toFixed(2)}
                    </span>
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>

          {/* Explanation */}
          <div className="bg-surface-elevated rounded-lg p-4 border border-border">
            <h4 className="text-[10px] font-medium text-text-muted uppercase tracking-wider mb-2">
              Why This Works
            </h4>
            <p className="text-sm text-text-secondary">
              {opportunity.reasoning ||
                `These ${opportunity.num_markets} outcomes are mutually exclusive and exhaustive - exactly one must occur. Since the combined cost ($${opportunity.total_cost.toFixed(2)}) is less than the payout ($1.00), buying all positions yields a $${opportunity.profit.toFixed(2)} profit margin regardless of which outcome occurs.`
              }
            </p>
          </div>

          {/* Risk Disclaimer */}
          <div className="text-[10px] text-text-muted bg-surface-elevated/50 rounded p-2 border border-border/50">
            <span className="font-medium">Note:</span> This analysis assumes markets resolve correctly and the platform operates normally.
            Actual returns may vary due to liquidity, slippage, resolution disputes, or platform risks.
          </div>

          {/* Confidence */}
          <div className="flex items-center justify-between text-xs text-text-muted bg-surface-elevated rounded p-3 border border-border">
            <span>Model Confidence</span>
            <div className="flex items-center gap-2">
              <div className="w-20 h-1.5 bg-surface rounded-full overflow-hidden">
                <div
                  className="h-full bg-cyan rounded-full"
                  style={{ width: `${opportunity.confidence * 100}%` }}
                />
              </div>
              <span className="font-mono">{Math.round(opportunity.confidence * 100)}%</span>
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex gap-2 pt-2">
            {opportunity.positions.map((position, idx) => (
              <a
                key={idx}
                href={position.market_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 text-center px-3 py-2 bg-surface-elevated border border-border rounded text-xs text-text-secondary hover:text-cyan hover:border-cyan/50 transition-colors"
              >
                {position.position} on Market {idx + 1} →
              </a>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
