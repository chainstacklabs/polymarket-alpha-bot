'use client'

import { useEffect, useState, useCallback } from 'react'
import { usePortfolioPrices, Portfolio } from '@/hooks/usePortfolioPrices'
import { PriceChangeIndicator, TierChangeBadge } from '@/components/PriceFlash'
import { PortfolioModal } from '@/components/PortfolioModal'
import { TIER_CONFIG } from '@/config/tier-config'
import { getApiBaseUrl } from '@/config/api-config'

// =============================================================================
// TYPES
// =============================================================================

type TierFilter = 'all' | 1 | 2 | 3 | 4
type SortField = 'coverage' | 'expected_profit' | 'total_cost' | 'tier'
type SortDirection = 'asc' | 'desc'

// =============================================================================
// MAIN COMPONENT
// =============================================================================

export default function PortfoliosPage() {
  // Global stats from REST (for accurate totals)
  const [globalStats, setGlobalStats] = useState<{
    total: number
    byTier: Record<string, number>
    profitableCount: number
  } | null>(null)

  // Local UI state
  const [tierFilter, setTierFilter] = useState<TierFilter>(2)
  const [profitableOnly, setProfitableOnly] = useState(false)
  const [sortField, setSortField] = useState<SortField>('coverage')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const [selectedPortfolio, setSelectedPortfolio] = useState<Portfolio | null>(null)
  const [filter, setFilter] = useState('')

  // Real-time portfolios from WebSocket
  const {
    portfolios,
    summary,
    connected,
    status,
    changedIds,
    priceChanges,
    tierChanges,
    updateFilters,
  } = usePortfolioPrices({
    maxTier: tierFilter === 'all' ? 3 : tierFilter,
    profitableOnly,
  })

  // Fetch global stats on mount (for accurate tier totals)
  useEffect(() => {
    fetch(`${getApiBaseUrl()}/data/portfolios?limit=1&max_tier=3`)
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data) {
          setGlobalStats({
            total: data.meta?.count || data.total_count || 0,
            byTier: data.meta?.by_tier || {},
            profitableCount: data.meta?.profitable_count || 0,
          })
        }
      })
      .catch(() => {})
  }, [])

  // Update WebSocket filters when UI filters change
  useEffect(() => {
    updateFilters({
      maxTier: tierFilter === 'all' ? 3 : tierFilter,
      profitableOnly,
    })
  }, [tierFilter, profitableOnly, updateFilters])

  // Handle sort
  const handleSort = useCallback((field: SortField) => {
    if (sortField === field) {
      setSortDirection(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDirection(field === 'tier' ? 'asc' : 'desc')
    }
  }, [sortField])

  // Filter and sort portfolios
  const sortedPortfolios = [...portfolios]
    .filter(p => {
      if (!filter) return true
      const search = filter.toLowerCase()
      return (
        p.target_question.toLowerCase().includes(search) ||
        p.cover_question.toLowerCase().includes(search) ||
        p.target_group_title.toLowerCase().includes(search) ||
        p.cover_group_title.toLowerCase().includes(search)
      )
    })
    .sort((a, b) => {
      let aVal: number, bVal: number
      switch (sortField) {
        case 'coverage':
          aVal = a.coverage
          bVal = b.coverage
          break
        case 'expected_profit':
          aVal = a.expected_profit
          bVal = b.expected_profit
          break
        case 'total_cost':
          aVal = a.total_cost
          bVal = b.total_cost
          break
        case 'tier':
          aVal = a.tier
          bVal = b.tier
          break
        default:
          aVal = a.coverage
          bVal = b.coverage
      }
      return sortDirection === 'asc' ? aVal - bVal : bVal - aVal
    })

  // Find tier change for a portfolio
  const getTierChange = (pairId: string) => {
    return tierChanges.find(tc => tc.pair_id === pairId)
  }

  // Use global stats if available, fallback to summary
  const totalCount = globalStats?.total || summary?.total || 0
  const profitableCount = globalStats?.profitableCount || summary?.profitable_count || 0
  const tierCounts = globalStats?.byTier || summary?.by_tier || {}

  const SortHeader = ({ field, label, hint, className = '' }: {
    field: SortField
    label: string
    hint: string
    className?: string
  }) => (
    <th
      className={`px-2.5 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted cursor-pointer hover:text-text-secondary transition-colors ${className}`}
      onClick={() => handleSort(field)}
      title={hint}
    >
      <div className="flex items-center gap-1">
        {label}
        {sortField === field && (
          <span className={`text-cyan ${sortDirection === 'desc' ? 'rotate-180' : ''}`}>↑</span>
        )}
      </div>
    </th>
  )

  return (
    <>
      <div className="space-y-4 animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-text-primary">Explore</h1>
            <p className="text-sm text-text-muted mt-0.5">
              Hedging strategies table
            </p>
          </div>
          <div className="flex items-center gap-3">
            {/* Tier change notifications */}
            {tierChanges.length > 0 && (
              <div className="flex items-center gap-1">
                {tierChanges.slice(0, 2).map(tc => (
                  <TierChangeBadge
                    key={tc.pair_id}
                    oldTier={tc.old_tier}
                    newTier={tc.new_tier}
                  />
                ))}
                {tierChanges.length > 2 && (
                  <span className="text-xs text-text-muted">+{tierChanges.length - 2} more</span>
                )}
              </div>
            )}
            {/* Connection status with tooltip */}
            <div className="flex items-center gap-1.5 group/live relative">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  connected ? 'bg-emerald animate-pulse' : 'bg-text-muted'
                }`}
              />
              <span className="text-xs text-text-muted">
                {status === 'connecting' ? 'Connecting...' : connected ? 'Live prices' : 'Offline'}
              </span>
              {/* Live prices tooltip */}
              <div className="absolute right-0 top-6 w-72 p-3 bg-surface-elevated border border-border rounded-lg shadow-lg opacity-0 invisible group-hover/live:opacity-100 group-hover/live:visible transition-all z-50">
                <p className="text-[11px] font-medium text-text-primary mb-2">Live Price Tracking</p>
                <div className="space-y-2 text-[10px]">
                  <div className="flex items-start gap-2">
                    <span className="text-emerald">●</span>
                    <p className="text-text-secondary">
                      Prices stream in real-time via WebSocket from Polymarket&apos;s CLOB (order book).
                    </p>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="text-cyan">●</span>
                    <p className="text-text-secondary">
                      Rows flash <span className="text-emerald">green</span> or <span className="text-rose">red</span> when prices change.
                    </p>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="text-amber">●</span>
                    <p className="text-text-secondary">
                      LLM confidence &amp; return recalculate instantly as market prices move.
                    </p>
                  </div>
                </div>
                <p className="text-[9px] text-text-muted mt-2 pt-2 border-t border-border">
                  Only markets in displayed strategies are tracked — bandwidth efficient.
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Tier Filter Tabs */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1 p-1 bg-surface-elevated rounded-lg border border-border">
            <button
              onClick={() => setTierFilter('all')}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                tierFilter === 'all'
                  ? 'bg-surface text-text-primary shadow-sm'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              All
              <span className="ml-1.5 text-xs font-mono text-text-muted">{totalCount}</span>
            </button>
            {[1, 2, 3].map(tier => {
              const count = tierCounts[`tier_${tier}`] || 0
              const config = TIER_CONFIG[tier]
              return (
                <button
                  key={tier}
                  onClick={() => setTierFilter(tier as TierFilter)}
                  title={config.desc}
                  className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                    tierFilter === tier
                      ? `${config.bg} ${config.color} shadow-sm`
                      : 'text-text-muted hover:text-text-secondary'
                  }`}
                >
                  {config.label}
                  <span className={`ml-1.5 text-xs font-mono ${tierFilter === tier ? config.color : 'text-text-muted'}`}>
                    {count}
                  </span>
                </button>
              )
            })}
          </div>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={profitableOnly}
              onChange={(e) => setProfitableOnly(e.target.checked)}
              className="w-4 h-4 rounded border-border bg-surface-elevated text-cyan focus:ring-cyan/50"
            />
            <span className="text-sm text-text-secondary">Show only profitable</span>
          </label>

          <div className="flex-1" />

          <input
            type="text"
            placeholder="Search strategies..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="px-3 py-1.5 w-48 bg-surface-elevated border border-border rounded text-sm text-text-primary placeholder:text-text-muted focus:border-cyan/50 focus:outline-none transition-colors"
          />
        </div>

        {/* Table */}
        {status === 'connecting' && portfolios.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <span className="text-sm text-text-muted">Connecting to live prices...</span>
          </div>
        ) : (
          <div className="rounded-lg border border-border overflow-hidden bg-surface">
            <div className="overflow-x-auto">
              <table className="w-full table-fixed">
                <thead className="bg-surface-elevated border-b border-border">
                  <tr>
                    <SortHeader field="tier" label="Quality" hint="How reliable this strategy is (Excellent is best)" className="w-16" />
                    <th className="px-2.5 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[25%]" title="The main bet you want to make">
                      Target Bet
                    </th>
                    <th className="px-2.5 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[25%]" title="The backup bet that pays if target loses">
                      Backup Bet
                    </th>
                    <SortHeader field="coverage" label="LLM Conf." hint="LLM-derived payout probability based on detected market relationships" className="w-20" />
                    <SortHeader field="total_cost" label="Cost" hint="Total investment to buy both positions" className="w-16" />
                    <SortHeader field="expected_profit" label="Return" hint="Expected return on your investment" className="w-16" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {sortedPortfolios.map((p) => {
                    const tierConfig = TIER_CONFIG[p.tier]
                    const isProfitable = p.expected_profit > 0.001
                    const coveragePercent = (p.coverage * 100).toFixed(1)
                    const isChanged = changedIds.has(p.pair_id)
                    const priceChange = priceChanges.get(p.pair_id)
                    const tierChange = getTierChange(p.pair_id)

                    // Determine flash class
                    const flashClass = isChanged
                      ? priceChange?.direction === 'up'
                        ? 'animate-flash-up'
                        : priceChange?.direction === 'down'
                          ? 'animate-flash-down'
                          : 'animate-flash'
                      : ''

                    return (
                      <tr
                        key={p.pair_id}
                        className={`hover:bg-surface-hover transition-colors cursor-pointer ${flashClass}`}
                        onClick={() => setSelectedPortfolio(p)}
                      >
                        <td className="px-2.5 py-2">
                          <div className="flex items-center gap-1">
                            <span
                              className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${tierConfig.bg} ${tierConfig.color} border ${tierConfig.border}`}
                              title={tierConfig.desc}
                            >
                              {tierConfig.label}
                            </span>
                            {tierChange && (
                              <span className={`text-xs ${tierChange.new_tier < tierChange.old_tier ? 'text-emerald' : 'text-rose'}`}>
                                {tierChange.new_tier < tierChange.old_tier ? '↑' : '↓'}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-2.5 py-2">
                          <div className="space-y-0.5">
                            <p className="text-sm text-text-primary truncate" title={p.target_question}>
                              {p.target_question}
                            </p>
                            <div className="flex items-center gap-1">
                              <p className="text-[10px] text-text-muted">
                                {p.target_position} @ ${p.target_price.toFixed(2)}
                              </p>
                              {isChanged && priceChange && (
                                <PriceChangeIndicator direction={priceChange.direction} />
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="px-2.5 py-2">
                          <div className="space-y-0.5">
                            <p className="text-sm text-text-primary truncate" title={p.cover_question}>
                              {p.cover_question}
                            </p>
                            <div className="flex items-center gap-1">
                              <p className="text-[10px] text-text-muted">
                                {p.cover_position} @ ${p.cover_price.toFixed(2)}
                              </p>
                              {isChanged && priceChange && (
                                <PriceChangeIndicator direction={priceChange.direction} />
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="px-2.5 py-2">
                          <div className="space-y-1">
                            <span className={`text-sm font-mono ${p.coverage >= 0.95 ? 'text-emerald' : p.coverage >= 0.90 ? 'text-cyan' : 'text-text-secondary'}`}>
                              {coveragePercent}%
                            </span>
                            {/* Mini coverage bar */}
                            <div className="w-16 h-1 bg-surface-elevated rounded-full overflow-hidden">
                              <div
                                className={`h-full transition-all duration-500 ${p.coverage >= 0.95 ? 'bg-emerald' : p.coverage >= 0.90 ? 'bg-cyan' : 'bg-amber'}`}
                                style={{ width: `${Math.min(100, p.coverage * 100)}%` }}
                              />
                            </div>
                          </div>
                        </td>
                        <td className="px-2.5 py-2">
                          <span className="text-sm font-mono text-text-secondary">
                            ${p.total_cost.toFixed(2)}
                          </span>
                        </td>
                        <td className="px-2.5 py-2">
                          <span className={`text-sm font-mono font-medium ${isProfitable ? 'text-emerald' : 'text-rose'}`}>
                            {isProfitable ? '+' : ''}{(p.expected_profit * 100).toFixed(1)}%
                          </span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* Footer */}
            <div className="px-2.5 py-2 bg-surface-elevated border-t border-border flex items-center justify-between">
              <span className="text-[10px] text-text-muted">
                Showing {sortedPortfolios.length} strategies
                {connected && <span className="ml-2 text-emerald">● Live prices</span>}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Portfolio Detail Modal */}
      {selectedPortfolio && (
        <PortfolioModal
          portfolio={selectedPortfolio}
          onClose={() => setSelectedPortfolio(null)}
        />
      )}
    </>
  )
}
