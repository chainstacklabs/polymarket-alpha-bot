'use client'

import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { usePortfolioPrices, Portfolio } from '@/hooks/usePortfolioPrices'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'
import { useFavorites } from '@/hooks/useFavorites'
import { PortfolioModal } from '@/components/PortfolioModal'
import { PipelineDropdown } from '@/components/terminal/PipelineDropdown'
import { KeyboardShortcutsHelp } from '@/components/terminal/KeyboardShortcutsHelp'
import { DensityToggle, useDensity } from '@/components/terminal/DensityToggle'
import { ExportDropdown } from '@/components/terminal/ExportDropdown'
import { PortfolioTable, SortField, SortDirection } from '@/components/terminal/PortfolioTable'
import { getApiBaseUrl } from '@/config/api-config'

// =============================================================================
// TYPES
// =============================================================================

interface PortfolioStats {
  total: number
  profitable: number
  avgCoverage: number
}

// =============================================================================
// HELPERS
// =============================================================================

const formatTime = (isoString: string | null): string => {
  if (!isoString) return '—'
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

// =============================================================================
// MAIN COMPONENT
// =============================================================================

export default function TerminalPage() {
  // Global stats from REST (for accurate totals)
  const [stats, setStats] = useState<PortfolioStats>({
    total: 0,
    profitable: 0,
    avgCoverage: 0,
  })

  // Local UI state
  const [profitableOnly, setProfitableOnly] = useState(false)
  const [sortField, setSortField] = useState<SortField>('coverage')
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc')
  const [selectedPortfolio, setSelectedPortfolio] = useState<Portfolio | null>(null)
  const [filter, setFilter] = useState('')

  // Keyboard navigation state
  const [selectedIndex, setSelectedIndex] = useState<number>(-1)
  const [showHelp, setShowHelp] = useState(false)

  // Density preference
  const { density, toggle: toggleDensity } = useDensity()

  // Favorites
  const { isFavorite, toggleFavorite, favoriteIds, count: favoriteCount, clearAll: clearFavorites } = useFavorites()

  // Pipeline status
  const [lastRunTime, setLastRunTime] = useState<string | null>(null)

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

  // Fetch global stats on mount
  const fetchData = useCallback(async () => {
    try {
      const apiBase = getApiBaseUrl()
      const [statsRes, pipelineRes] = await Promise.all([
        fetch(`${apiBase}/data/portfolios?limit=1&max_tier=1`),
        fetch(`${apiBase}/pipeline/status`),
      ])

      if (statsRes.ok) {
        const data = await statsRes.json()
        const allPortfolios = data.data?.portfolios || []
        setStats({
          total: data.meta?.count || data.total_count || 0,
          profitable: data.meta?.profitable_count || data.profitable_count || 0,
          avgCoverage:
            allPortfolios.length > 0
              ? allPortfolios.reduce((acc: number, p: Portfolio) => acc + p.coverage, 0) /
                allPortfolios.length
              : 0,
        })
      }

      if (pipelineRes.ok) {
        const pipelineData = await pipelineRes.json()
        setLastRunTime(pipelineData?.production?.last_run?.completed_at || null)
      }
    } catch (error) {
      console.debug('Fetch error:', error)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [fetchData])

  // Update WebSocket filters when UI filters change
  useEffect(() => {
    updateFilters({
      maxTier: 1,
      profitableOnly,
    })
  }, [profitableOnly, updateFilters])

  // Handle sort
  const handleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
      } else {
        setSortField(field)
        setSortDirection('desc')
      }
    },
    [sortField]
  )

  // Filter and sort portfolios (favorites pinned to top)
  const sortedPortfolios = useMemo(() => {
    const filtered = [...portfolios].filter((p) => {
      if (!filter) return true
      const search = filter.toLowerCase()
      return (
        p.target_question.toLowerCase().includes(search) ||
        p.cover_question.toLowerCase().includes(search) ||
        p.target_group_title.toLowerCase().includes(search) ||
        p.cover_group_title.toLowerCase().includes(search)
      )
    })

    // Sort function
    const sortFn = (a: Portfolio, b: Portfolio) => {
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
        default:
          aVal = a.coverage
          bVal = b.coverage
      }
      return sortDirection === 'asc' ? aVal - bVal : bVal - aVal
    }

    // Separate favorites and non-favorites, sort each, then combine
    const favorites = filtered.filter((p) => isFavorite(p.pair_id)).sort(sortFn)
    const nonFavorites = filtered.filter((p) => !isFavorite(p.pair_id)).sort(sortFn)

    return [...favorites, ...nonFavorites]
  }, [portfolios, filter, sortField, sortDirection, isFavorite])

  // Count of pinned favorites in current view
  const pinnedCount = useMemo(() => {
    return sortedPortfolios.filter((p) => isFavorite(p.pair_id)).length
  }, [sortedPortfolios, isFavorite])

  // Reset selection when list changes
  useEffect(() => {
    if (selectedIndex >= sortedPortfolios.length) {
      setSelectedIndex(sortedPortfolios.length - 1)
    }
  }, [sortedPortfolios.length, selectedIndex])


  // Keyboard shortcuts
  useKeyboardShortcuts(
    {
      onNavigateDown: () => {
        setSelectedIndex((prev) => Math.min(prev + 1, sortedPortfolios.length - 1))
      },
      onNavigateUp: () => {
        setSelectedIndex((prev) => Math.max(prev - 1, 0))
      },
      onSelect: () => {
        if (selectedIndex >= 0 && selectedIndex < sortedPortfolios.length) {
          setSelectedPortfolio(sortedPortfolios[selectedIndex])
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

  // Use global stats if available, fallback to summary
  const totalCount = stats.total || summary?.total || 0
  const profitableCount = stats.profitable || summary?.profitable_count || 0

  return (
    <>
      <div className="flex flex-col h-full gap-4 animate-fade-in">
        {/* Header with Stats Bar */}
        <header className="bg-surface border border-border rounded-lg p-3">
          <div className="flex items-center justify-between">
            {/* Left: Title + key metrics */}
            <div className="flex items-center gap-6">
              <div>
                <h1 className="text-lg font-semibold text-text-primary">Terminal</h1>
                <p className="text-[10px] text-text-muted">
                  Unified trading workspace •{' '}
                  <button
                    onClick={() => setShowHelp(true)}
                    className="text-cyan hover:underline"
                  >
                    ? shortcuts
                  </button>
                </p>
              </div>

              <div className="w-px h-10 bg-border" />

              <div className="flex items-center gap-2">
                <span className="text-2xl font-semibold font-mono text-cyan">{totalCount}</span>
                <div className="text-xs text-text-muted leading-tight">
                  <p>strategies</p>
                  <p className="text-text-muted/70">{profitableCount} profitable</p>
                </div>
              </div>

              <div className="w-px h-8 bg-border" />

              <div className="flex items-center gap-2 group/avgconf relative">
                <span className="text-2xl font-semibold font-mono text-amber">
                  {stats.avgCoverage > 0 ? `${(stats.avgCoverage * 100).toFixed(0)}%` : '—'}
                </span>
                <span className="text-xs text-text-muted flex items-center gap-1">
                  avg LLM confidence
                  <span className="w-3.5 h-3.5 rounded-full bg-surface border border-border text-[9px] flex items-center justify-center opacity-50 group-hover/avgconf:opacity-100 transition-opacity cursor-help">?</span>
                </span>
                {/* Tooltip */}
                <div className="absolute left-0 top-full mt-2 w-72 p-2.5 bg-surface-elevated border border-border rounded-lg shadow-lg opacity-0 invisible group-hover/avgconf:opacity-100 group-hover/avgconf:visible transition-all z-50">
                  <p className="text-[11px] font-medium text-violet-400 mb-1.5">LLM Confidence Score</p>
                  <p className="text-[10px] text-text-secondary mb-2">
                    Probability of $1 payout, derived from LLM-detected logical relationships between markets.
                  </p>
                  <div className="text-[10px] text-text-muted space-y-1 border-t border-border pt-2">
                    <p><span className="text-violet-400">1.</span> LLM extracts implications (A→B) between markets</p>
                    <p><span className="text-violet-400">2.</span> Maps strength → probability: <span className="text-text-secondary">necessary</span>=98%, <span className="text-text-secondary">strong</span>=85%</p>
                    <p><span className="text-violet-400">3.</span> Formula: P(target) + P(¬target) × P(cover)</p>
                  </div>
                </div>
              </div>
            </div>

            {/* Right: Status indicators */}
            <div className="flex items-center gap-3">
              {lastRunTime && (
                <span
                  className="text-xs text-text-muted cursor-help"
                  title="When the pipeline last analyzed markets for arbitrage opportunities"
                >
                  Markets scanned {formatTime(lastRunTime)}
                </span>
              )}

              {/* Connection status */}
              <div className="flex items-center gap-1.5">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    connected ? 'bg-emerald animate-pulse' : 'bg-text-muted'
                  }`}
                />
                <span className="text-xs text-text-muted">
                  {status === 'connecting' ? 'Connecting...' : connected ? 'Live prices' : 'Offline'}
                </span>
              </div>

              {/* Pipeline dropdown */}
              <PipelineDropdown />
            </div>
          </div>
        </header>

        {/* Filters Row */}
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 cursor-pointer" title="Press p to toggle">
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
                <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
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

          {/* Export */}
          <ExportDropdown portfolios={sortedPortfolios} />

          {/* Density toggle */}
          <DensityToggle density={density} onToggle={toggleDensity} />

          <input
            ref={searchInputRef}
            type="text"
            placeholder="Search... (press /)"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="px-3 py-1.5 w-48 bg-surface-elevated border border-border rounded text-sm text-text-primary placeholder:text-text-muted focus:border-cyan/50 focus:outline-none transition-colors"
          />
        </div>

        {/* Portfolio Table */}
        {status === 'connecting' && portfolios.length === 0 ? (
          <div className="flex-1 flex items-center justify-center">
            <span className="text-sm text-text-muted">Connecting to live prices...</span>
          </div>
        ) : portfolios.length === 0 && stats.total === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center border border-border rounded-lg bg-surface">
            <p className="text-sm text-text-secondary mb-1">No strategies found yet</p>
            <p className="text-xs text-text-muted mb-4">
              Run the pipeline to discover hedging opportunities
            </p>
          </div>
        ) : (
          <PortfolioTable
            portfolios={sortedPortfolios}
            density={density}
            sortField={sortField}
            sortDirection={sortDirection}
            selectedIndex={selectedIndex}
            changedIds={changedIds}
            priceChanges={priceChanges}
            pinnedCount={pinnedCount}
            connected={connected}
            isFavorite={isFavorite}
            onSort={handleSort}
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
        <PortfolioModal portfolio={selectedPortfolio} onClose={() => setSelectedPortfolio(null)} />
      )}

      {/* Keyboard Shortcuts Help Modal */}
      <KeyboardShortcutsHelp isOpen={showHelp} onClose={() => setShowHelp(false)} />
    </>
  )
}
