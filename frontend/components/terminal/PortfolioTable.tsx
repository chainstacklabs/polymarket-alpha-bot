'use client'

import { useRef, useCallback, useEffect, useState } from 'react'
import { Portfolio } from '@/hooks/usePortfolioPrices'
import { PriceChangeIndicator } from '@/components/PriceFlash'
import { FavoriteButton } from '@/components/terminal/QuickActions'
import { densityStyles, Density } from '@/components/terminal/DensityToggle'

// =============================================================================
// TYPES
// =============================================================================

type SortField = 'coverage' | 'expected_profit' | 'total_cost'
type SortDirection = 'asc' | 'desc'

interface PriceChange {
  direction: 'up' | 'down' | 'changed' | null
}

interface PortfolioTableProps {
  portfolios: Portfolio[]
  density: Density
  sortField: SortField
  sortDirection: SortDirection
  selectedIndex: number
  changedIds: Set<string>
  priceChanges: Map<string, PriceChange>
  pinnedCount: number
  connected: boolean
  isFavorite: (pairId: string) => boolean
  onSort: (field: SortField) => void
  onSelect: (index: number, portfolio: Portfolio) => void
  onToggleFavorite: (pairId: string, coverage: number) => void
}

// =============================================================================
// MAIN COMPONENT
// =============================================================================

export function PortfolioTable({
  portfolios,
  density,
  sortField,
  sortDirection,
  selectedIndex,
  changedIds,
  priceChanges,
  pinnedCount,
  connected,
  isFavorite,
  onSort,
  onSelect,
  onToggleFavorite,
}: PortfolioTableProps) {
  const styles = densityStyles[density]
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const rowRefs = useRef<Map<number, HTMLTableRowElement>>(new Map())
  const [scrollState, setScrollState] = useState({ atTop: true, atBottom: true })

  // Track scroll position for shadow indicators
  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return

    const { scrollTop, scrollHeight, clientHeight } = container
    const atTop = scrollTop < 10
    const atBottom = scrollTop + clientHeight >= scrollHeight - 10

    setScrollState((prev) => {
      if (prev.atTop !== atTop || prev.atBottom !== atBottom) {
        return { atTop, atBottom }
      }
      return prev
    })
  }, [])

  // Check scroll state on mount and when portfolios change
  useEffect(() => {
    handleScroll()
  }, [portfolios.length, handleScroll])

  // Auto-scroll to selected row
  useEffect(() => {
    if (selectedIndex >= 0) {
      const row = rowRefs.current.get(selectedIndex)
      if (row) {
        row.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      }
    }
  }, [selectedIndex])

  const SortHeader = ({
    field,
    label,
    hint,
    className = '',
  }: {
    field: SortField
    label: string
    hint: string
    className?: string
  }) => (
    <th
      className={`${styles.headerPadding} text-left text-[10px] font-medium uppercase tracking-wider text-text-muted cursor-pointer hover:text-text-secondary transition-colors ${className}`}
      onClick={() => onSort(field)}
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
    <div className="flex flex-col flex-1 min-h-0 rounded-lg border border-border overflow-hidden bg-surface">
      {/* Scroll container with shadow indicators */}
      <div className="relative flex-1 min-h-0">
        {/* Top scroll shadow */}
        <div
          className={`absolute top-0 left-0 right-0 h-6 bg-gradient-to-b from-surface/90 to-transparent z-20 pointer-events-none transition-opacity duration-150 ${
            scrollState.atTop ? 'opacity-0' : 'opacity-100'
          }`}
          aria-hidden="true"
        />

        {/* Bottom scroll shadow */}
        <div
          className={`absolute bottom-0 left-0 right-0 h-6 bg-gradient-to-t from-surface/90 to-transparent z-20 pointer-events-none transition-opacity duration-150 ${
            scrollState.atBottom ? 'opacity-0' : 'opacity-100'
          }`}
          aria-hidden="true"
        />

        <div
          ref={scrollContainerRef}
          className="h-full overflow-y-auto overflow-x-auto"
          onScroll={handleScroll}
        >
          <table className="w-full table-fixed">
            <thead className="bg-surface-elevated border-b border-border sticky top-0 z-10">
              <tr>
                <th
                  className={`${styles.headerPadding} text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-10`}
                  title="Watch this strategy"
                >
                  ★
                </th>
                <th
                  className={`${styles.headerPadding} text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[28%]`}
                  title="The main bet"
                >
                  Target Bet
                </th>
                <th
                  className={`${styles.headerPadding} text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[28%]`}
                  title="The backup bet"
                >
                  Backup Bet
                </th>
                <SortHeader
                  field="coverage"
                  label="LLM Conf."
                  hint="LLM-derived payout probability"
                  className="w-20"
                />
                <SortHeader
                  field="total_cost"
                  label="Cost"
                  hint="Total investment"
                  className="w-16"
                />
                <SortHeader
                  field="expected_profit"
                  label="Return"
                  hint="Expected return"
                  className="w-16"
                />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {portfolios.map((p, index) => {
                const isProfitable = p.expected_profit > 0.001
                const coveragePercent = (p.coverage * 100).toFixed(1)
                const isChanged = changedIds.has(p.pair_id)
                const priceChange = priceChanges.get(p.pair_id)
                const isSelected = index === selectedIndex
                const isPinned = isFavorite(p.pair_id)

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
                    ref={(el) => {
                      if (el) rowRefs.current.set(index, el)
                    }}
                    className={`
                      group transition-colors cursor-pointer ${flashClass}
                      ${isSelected
                        ? 'bg-cyan/10 ring-1 ring-inset ring-cyan/50'
                        : isPinned
                          ? 'bg-amber/5 hover:bg-amber/10'
                          : 'hover:bg-surface-hover'
                      }
                    `}
                    onClick={() => onSelect(index, p)}
                  >
                    <td className={styles.cellPadding}>
                      <FavoriteButton
                        isFavorite={isPinned}
                        onToggle={() => onToggleFavorite(p.pair_id, p.coverage)}
                      />
                    </td>
                    <td className={styles.cellPadding}>
                      <div className="space-y-0.5">
                        <p
                          className={`${styles.fontSize} text-text-primary truncate`}
                          title={p.target_question}
                        >
                          {p.target_question}
                        </p>
                        <div className="flex items-center gap-1">
                          <p className="text-[10px] text-text-muted">
                            {p.target_position} @ ${p.target_price.toFixed(2)}
                          </p>
                          {isChanged && priceChange && (
                            <PriceChangeIndicator direction={priceChange.direction === 'changed' ? null : priceChange.direction} />
                          )}
                        </div>
                      </div>
                    </td>
                    <td className={styles.cellPadding}>
                      <div className="space-y-0.5">
                        <p
                          className={`${styles.fontSize} text-text-primary truncate`}
                          title={p.cover_question}
                        >
                          {p.cover_question}
                        </p>
                        <div className="flex items-center gap-1">
                          <p className="text-[10px] text-text-muted">
                            {p.cover_position} @ ${p.cover_price.toFixed(2)}
                          </p>
                          {isChanged && priceChange && (
                            <PriceChangeIndicator direction={priceChange.direction === 'changed' ? null : priceChange.direction} />
                          )}
                        </div>
                      </div>
                    </td>
                    <td className={styles.cellPadding}>
                      <div className="space-y-1">
                        <span
                          className={`${styles.fontSize} font-mono ${p.coverage >= 0.95 ? 'text-emerald' : p.coverage >= 0.9 ? 'text-cyan' : 'text-text-secondary'}`}
                        >
                          {coveragePercent}%
                        </span>
                        <div className="w-16 h-1 bg-surface-elevated rounded-full overflow-hidden">
                          <div
                            className={`h-full transition-all duration-500 ${p.coverage >= 0.95 ? 'bg-emerald' : p.coverage >= 0.9 ? 'bg-cyan' : 'bg-amber'}`}
                            style={{ width: `${Math.min(100, p.coverage * 100)}%` }}
                          />
                        </div>
                      </div>
                    </td>
                    <td className={styles.cellPadding}>
                      <span className={`${styles.fontSize} font-mono text-text-secondary`}>
                        ${p.total_cost.toFixed(2)}
                      </span>
                    </td>
                    <td className={styles.cellPadding}>
                      <span
                        className={`${styles.fontSize} font-mono font-medium ${isProfitable ? 'text-emerald' : 'text-rose'}`}
                      >
                        {isProfitable ? '+' : ''}
                        {(p.expected_profit * 100).toFixed(1)}%
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Footer */}
      <div className="px-2.5 py-2 bg-surface-elevated border-t border-border flex items-center justify-between shrink-0">
        <span className="text-[10px] text-text-muted">
          Showing {portfolios.length} strategies
          {pinnedCount > 0 && (
            <span className="ml-2 text-amber">★ {pinnedCount} pinned</span>
          )}
          {connected && <span className="ml-2 text-emerald">● Live prices</span>}
          {selectedIndex >= 0 && (
            <span className="ml-2 text-cyan">• Row {selectedIndex + 1} selected</span>
          )}
        </span>
        <span className="text-[10px] text-text-muted">
          Press <kbd className="px-1 py-0.5 bg-surface border border-border rounded text-[9px]">?</kbd> for shortcuts
        </span>
      </div>
    </div>
  )
}

// Re-export types for consumers
export type { SortField, SortDirection, PriceChange }
