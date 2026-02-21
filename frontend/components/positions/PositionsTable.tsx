'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import type { Position } from '@/app/positions/page'
import { PositionTableRow } from './PositionTableRow'

interface PositionsTableProps {
  positions: Position[]
  onRefresh: () => void
}

export function PositionsTable({ positions, onRefresh }: PositionsTableProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [scrollState, setScrollState] = useState({
    atTop: true,
    atBottom: true,
  })

  const handleScroll = useCallback(() => {
    const container = scrollContainerRef.current
    if (!container) return
    const { scrollTop, scrollHeight, clientHeight } = container
    setScrollState({
      atTop: scrollTop < 10,
      atBottom: scrollTop + clientHeight >= scrollHeight - 10,
    })
  }, [])

  useEffect(() => {
    handleScroll()
  }, [positions.length, handleScroll])

  return (
    <div className="flex flex-col flex-1 min-h-0 rounded-lg border border-border overflow-hidden bg-surface">
      <div className="relative flex-1 min-h-0">
        {/* Top shadow */}
        <div
          className={`absolute top-0 left-0 right-0 h-6 bg-gradient-to-b from-surface/90 to-transparent z-20 pointer-events-none transition-opacity ${
            scrollState.atTop ? 'opacity-0' : 'opacity-100'
          }`}
        />
        {/* Bottom shadow */}
        <div
          className={`absolute bottom-0 left-0 right-0 h-6 bg-gradient-to-t from-surface/90 to-transparent z-20 pointer-events-none transition-opacity ${
            scrollState.atBottom ? 'opacity-0' : 'opacity-100'
          }`}
        />

        <div
          ref={scrollContainerRef}
          className="h-full overflow-y-auto"
          onScroll={handleScroll}
        >
          <table className="w-full table-fixed">
            <thead className="bg-surface-elevated border-b border-border sticky top-0 z-10">
              <tr>
                <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[4%]">
                  Status
                </th>
                <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[58%]">
                  Position
                </th>
                <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[8%]">
                  Tokens
                </th>
                <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[6%]">
                  Entry
                </th>
                <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[6%]">
                  Value
                </th>
                <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[9%]">
                  P&L
                </th>
                <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[5%]">
                  Age
                </th>
                <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[4%]">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {positions.map((position) => (
                <PositionTableRow
                  key={position.position_id}
                  position={position}
                  onRefresh={onRefresh}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Footer */}
      <div className="px-3 py-2 bg-surface-elevated border-t border-border flex items-center justify-between shrink-0">
        <span className="text-[10px] text-text-muted">
          {positions.length} position{positions.length !== 1 ? 's' : ''}
        </span>
      </div>
    </div>
  )
}
