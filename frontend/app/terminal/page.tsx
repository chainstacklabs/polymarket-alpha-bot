'use client'

import {
  useEffect,
  useState,
  useCallback,
  useRef,
  useMemo,
  useDeferredValue,
} from 'react'
import { usePortfolioPrices, Portfolio } from '@/hooks/usePortfolioPrices'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { useFavorites } from '@/hooks/useFavorites'
import { PortfolioModal } from '@/components/PortfolioModal'
import { KeyboardShortcutsHelp } from '@/components/terminal/KeyboardShortcutsHelp'
import { PortfolioTable } from '@/components/terminal/PortfolioTable'
import { StatusIndicators } from '@/components/StatusIndicators'
import { getApiBaseUrl } from '@/config/api-config'

// =============================================================================
// TYPES
// =============================================================================

interface PortfolioStats {
  total: number
  profitable: number
}

// =============================================================================
// HELPERS
// =============================================================================

async function loadPortfolioStats(
  apiBase: string
): Promise<PortfolioStats | null> {
  const res = await fetch(`${apiBase}/data/portfolios?limit=1&max_tier=1`)
  if (!res.ok) return null
  const data = await res.json()
  return {
    total: data.meta?.count || data.total_count || 0,
    profitable: data.meta?.profitable_count || data.profitable_count || 0,
  }
}

// =============================================================================
// MAIN COMPONENT
// =============================================================================

export default function TerminalPage() {
  // Global stats from REST (for accurate totals)
  const [stats, setStats] = useState<PortfolioStats>({
    total: 0,
    profitable: 0,
  })

  // Local UI state
  const [profitableOnly, setProfitableOnly] = useState(false)
  const [selectedPortfolio, setSelectedPortfolio] = useState<Portfolio | null>(
    null
  )
  const [filter, setFilter] = useState('')
  // Defer filter value to avoid blocking UI during expensive sort/filter operations
  const deferredFilter = useDeferredValue(filter)

  // Keyboard navigation state
  const [selectedIndex, setSelectedIndex] = useState<number>(-1)
  const [showHelp, setShowHelp] = useState(false)

  // Fixed density
  const density = 'comfortable' as const

  // Favorites
  const {
    favoriteSet,
    toggleFavorite,
    count: favoriteCount,
    clearAll: clearFavorites,
  } = useFavorites()

  // Refs
  const searchInputRef = useRef<HTMLInputElement>(null)

  // Real-time portfolios from WebSocket
  const {
    portfolios,
    summary,
    connected,
    status,
    changedIds,
    priceChanges,
    updateFilters,
  } = usePortfolioPrices({
    maxTier: 1,
    profitableOnly,
  })

  // Manual refresh for keyboard shortcut (r key)
  const fetchData = useCallback(async () => {
    try {
      const result = await loadPortfolioStats(getApiBaseUrl())
      if (result) setStats(result)
    } catch (error) {
      console.debug('Fetch error:', error)
    }
  }, [])

  // Polling on mount — function defined inside effect to satisfy React rules
  useEffect(() => {
    async function pollStats() {
      try {
        const result = await loadPortfolioStats(getApiBaseUrl())
        if (result) setStats(result)
      } catch (error) {
        console.debug('Fetch error:', error)
      }
    }

    pollStats()
    const interval = setInterval(pollStats, 30000)
    return () => clearInterval(interval)
  }, [])

  // Track if this is the initial mount to avoid duplicate fetch
  // (WebSocket already sends 'initial' with default filters on connect)
  const isInitialMount = useRef(true)

  // Update WebSocket filters when UI filters change
  // Skip on initial mount - WebSocket 'initial' message handles first load
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false
      return
    }
    updateFilters({
      maxTier: 1,
      profitableOnly,
    })
  }, [profitableOnly, updateFilters])

  // Filter and sort portfolios (favorites pinned to top)
  // Uses deferredFilter to avoid blocking UI during typing
  const sortedPortfolios = useMemo(() => {
    const filtered = [...portfolios].filter((p) => {
      if (!deferredFilter) return true
      const search = deferredFilter.toLowerCase()
      return (
        p.target_question.toLowerCase().includes(search) ||
        p.cover_question.toLowerCase().includes(search) ||
        p.target_group_title.toLowerCase().includes(search) ||
        p.cover_group_title.toLowerCase().includes(search)
      )
    })

    // Multi-field sort: viability_score (desc), then expected_profit (desc)
    // This keeps portfolios stable within each LLM confidence tier while
    // allowing profitability ordering to update as prices change
    const sortFn = (a: Portfolio, b: Portfolio) => {
      // Primary sort: viability_score descending (undefined values at end)
      const aScore = a.viability_score ?? -1
      const bScore = b.viability_score ?? -1
      if (aScore !== bScore) {
        return bScore - aScore // descending
      }

      // Secondary sort: expected_profit descending
      if (a.expected_profit !== b.expected_profit) {
        return b.expected_profit - a.expected_profit // descending
      }

      // Tie-breaker: pair_id for stable sorting
      return a.pair_id.localeCompare(b.pair_id)
    }

    // Separate favorites and non-favorites, sort each, then combine
    // Favorites always appear at the top, maintaining sort order within each group
    const favorites = filtered
      .filter((p) => favoriteSet.has(p.pair_id))
      .sort(sortFn)
    const nonFavorites = filtered
      .filter((p) => !favoriteSet.has(p.pair_id))
      .sort(sortFn)

    return [...favorites, ...nonFavorites]
  }, [portfolios, deferredFilter, favoriteSet])

  // Count of pinned favorites in current view
  const pinnedCount = useMemo(() => {
    return sortedPortfolios.filter((p) => favoriteSet.has(p.pair_id)).length
  }, [sortedPortfolios, favoriteSet])

  // Clamp selectedIndex to valid range (computed during render, not in an effect)
  const effectiveSelectedIndex =
    sortedPortfolios.length === 0
      ? -1
      : Math.min(selectedIndex, sortedPortfolios.length - 1)

  // Keyboard shortcuts
  useKeyboardShortcuts(
    {
      onNavigateDown: () => {
        setSelectedIndex((prev) =>
          Math.min(prev + 1, sortedPortfolios.length - 1)
        )
      },
      onNavigateUp: () => {
        setSelectedIndex((prev) => Math.max(prev - 1, 0))
      },
      onSelect: () => {
        if (
          effectiveSelectedIndex >= 0 &&
          effectiveSelectedIndex < sortedPortfolios.length
        ) {
          setSelectedPortfolio(sortedPortfolios[effectiveSelectedIndex])
        }
      },
      onClose: () => {
        if (selectedPortfolio) {
          setSelectedPortfolio(null)
        } else if (showHelp) {
          setShowHelp(false)
        }
      },
      onToggleProfitable: () => setProfitableOnly((prev) => !prev),
      onFocusSearch: () => searchInputRef.current?.focus(),
      onRefresh: () => fetchData(),
      onShowHelp: () => setShowHelp(true),
    },
    { enabled: !showHelp, searchInputRef }
  )

  // Calculate stats from WebSocket summary (real-time) with REST fallback
  // WebSocket summary now includes by_tier breakdown for accurate tier-specific counts
  const totalCount = useMemo(() => {
    // Prefer WebSocket summary for tier 1 count (real-time updates)
    if (connected && summary?.by_tier?.tier_1 !== undefined) {
      return summary.by_tier.tier_1
    }
    // Fall back to REST stats
    return stats.total || summary?.total || 0
  }, [connected, summary, stats.total])

  const profitableCount = useMemo(() => {
    // Count profitable tier 1 portfolios from current data
    // The portfolios array contains tier 1 portfolios (maxTier=1 filter)
    if (connected && portfolios.length > 0) {
      // Count profitable from the current portfolio list
      // Note: when profitableOnly=false, portfolios contains all tier 1 portfolios
      // when profitableOnly=true, portfolios only contains profitable ones
      if (!profitableOnly) {
        // Full list - count profitable directly
        return portfolios.filter((p) => p.expected_profit > 0.001).length
      }
      // If profitableOnly is true, we need the total profitable count
      // Use REST stats as fallback since we don't have the full list
      return stats.profitable || portfolios.length
    }
    return stats.profitable || summary?.profitable_count || 0
  }, [
    connected,
    portfolios,
    profitableOnly,
    stats.profitable,
    summary?.profitable_count,
  ])

  return (
    <>
      <div className="flex flex-col h-full gap-4 animate-fade-in">
        {/* Header with Stats Bar */}
        <header className="bg-surface border border-border rounded-lg p-3">
          <div className="flex items-center justify-between">
            {/* Left: Title + key metrics */}
            <div className="flex items-center gap-6">
              <div>
                <h1 className="text-lg font-semibold text-text-primary">
                  Terminal
                </h1>
                <p className="text-[10px] text-text-muted">
                  Unified trading workspace
                </p>
              </div>

              <div className="w-px h-10 bg-border" />

              <div className="flex items-center gap-2">
                <span className="text-2xl font-semibold font-mono text-cyan">
                  {totalCount}
                </span>
                <div className="text-xs text-text-muted leading-tight">
                  <p>strategies</p>
                  <p className="text-text-muted/70">
                    {profitableCount} profitable
                  </p>
                </div>
              </div>
            </div>

            {/* Right: Status indicators */}
            <StatusIndicators connected={connected} connectionStatus={status} />
          </div>
        </header>

        {/* Filters Row */}
        <div className="flex items-center gap-4">
          <label
            className="flex items-center gap-2 cursor-pointer"
            title="Press p to toggle"
          >
            <input
              type="checkbox"
              checked={profitableOnly}
              onChange={(e) => setProfitableOnly(e.target.checked)}
              className="w-4 h-4 rounded border-border bg-surface-elevated text-cyan focus:ring-cyan/50"
            />
            <span className="text-sm text-text-secondary">Profitable only</span>
          </label>

          {/* Favorites indicator */}
          {favoriteCount > 0 && (
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1 text-xs text-amber">
                <svg
                  className="w-3.5 h-3.5"
                  fill="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z" />
                </svg>
                {favoriteCount} watching
              </span>
              <button
                onClick={clearFavorites}
                className="text-[10px] text-text-muted hover:text-rose transition-colors"
              >
                Clear all
              </button>
            </div>
          )}

          <div className="flex-1" />

          <div className="relative">
            <input
              ref={searchInputRef}
              type="text"
              placeholder="Search... (press /)"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className={`px-3 py-1.5 w-48 bg-surface-elevated border border-border rounded text-sm text-text-primary placeholder:text-text-muted focus:border-cyan/50 focus:outline-none transition-colors ${filter !== deferredFilter ? 'opacity-70' : ''}`}
            />
            {filter !== deferredFilter && (
              <div className="absolute right-2 top-1/2 -translate-y-1/2">
                <div className="w-3 h-3 border border-cyan border-t-transparent rounded-full animate-spin" />
              </div>
            )}
          </div>
        </div>

        {/* Portfolio Table */}
        {status === 'connecting' && portfolios.length === 0 ? (
          <div className="flex-1 flex items-center justify-center">
            <span className="text-sm text-text-muted">
              Connecting to live prices...
            </span>
          </div>
        ) : portfolios.length === 0 && stats.total === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center border border-border rounded-lg bg-surface">
            <p className="text-sm text-text-secondary mb-1">
              No strategies found yet
            </p>
            <p className="text-xs text-text-muted mb-4">
              Run the pipeline to discover hedging opportunities
            </p>
          </div>
        ) : (
          <PortfolioTable
            portfolios={sortedPortfolios}
            density={density}
            selectedIndex={effectiveSelectedIndex}
            changedIds={changedIds}
            priceChanges={priceChanges}
            pinnedCount={pinnedCount}
            connected={connected}
            favoriteSet={favoriteSet}
            onSelect={(index, portfolio) => {
              setSelectedIndex(index)
              setSelectedPortfolio(portfolio)
            }}
            onToggleFavorite={toggleFavorite}
          />
        )}
      </div>

      {/* Portfolio Detail Modal */}
      {selectedPortfolio && (
        <PortfolioModal
          portfolio={selectedPortfolio}
          onClose={() => setSelectedPortfolio(null)}
        />
      )}

      {/* Keyboard Shortcuts Help Modal */}
      <KeyboardShortcutsHelp
        isOpen={showHelp}
        onClose={() => setShowHelp(false)}
      />
    </>
  )
}
