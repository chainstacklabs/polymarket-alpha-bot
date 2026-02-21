'use client'

import { useState, useRef, useEffect } from 'react'
import { getApiBaseUrl } from '@/config/api-config'
import type { Position } from '@/app/positions/page'

interface PositionActionsDropdownProps {
  position: Position
  onRefresh: () => void
}

export function PositionActionsDropdown({
  position: p,
  onRefresh,
}: PositionActionsDropdownProps) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    if (open) document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open])

  const canSellTarget = p.target_balance > 0.01 && !p.selling_target
  const canSellCover = p.cover_balance > 0.01 && !p.selling_cover
  const canMerge =
    Math.min(p.target_balance, p.target_unwanted_balance) > 0.01 ||
    Math.min(p.cover_balance, p.cover_unwanted_balance) > 0.01

  // Use persisted selling state (survives page refresh)
  const isSellingTarget = p.selling_target || loading === 'sell-target'
  const isSellingCover = p.selling_cover || loading === 'sell-cover'

  const handleSell = async (side: 'target' | 'cover') => {
    setLoading(`sell-${side}`)
    setError(null)
    try {
      const res = await fetch(
        `${getApiBaseUrl()}/positions/${p.position_id}/sell`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ side, token_type: 'wanted' }),
        }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Sell failed')
      if (!data.success) throw new Error(data.error || 'Order not filled')
      setOpen(false)
      onRefresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setLoading(null)
    }
  }

  const handleMerge = async (side: 'target' | 'cover') => {
    setLoading(`merge-${side}`)
    setError(null)
    try {
      const res = await fetch(
        `${getApiBaseUrl()}/positions/${p.position_id}/merge`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ side }),
        }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Merge failed')
      if (!data.success) throw new Error(data.error || 'Merge failed')
      setOpen(false)
      onRefresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setOpen(!open)}
        className="p-1 text-text-muted hover:text-text-primary rounded hover:bg-surface-elevated transition-colors"
      >
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
          <circle cx="12" cy="6" r="2" />
          <circle cx="12" cy="12" r="2" />
          <circle cx="12" cy="18" r="2" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 w-48 bg-surface-elevated border border-border rounded-lg shadow-xl z-50 py-1">
          {error && (
            <div className="px-3 py-2 text-xs text-rose border-b border-border">
              {error}
            </div>
          )}

          <button
            onClick={() => handleSell('target')}
            disabled={!canSellTarget || loading !== null || isSellingTarget}
            className="w-full px-3 py-2 text-left text-sm hover:bg-surface disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-between"
          >
            <span>{isSellingTarget ? 'Selling...' : 'Sell Target'}</span>
            {isSellingTarget && (
              <span className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
            )}
          </button>

          <button
            onClick={() => handleSell('cover')}
            disabled={!canSellCover || loading !== null || isSellingCover}
            className="w-full px-3 py-2 text-left text-sm hover:bg-surface disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-between"
          >
            <span>{isSellingCover ? 'Selling...' : 'Sell Cover'}</span>
            {isSellingCover && (
              <span className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
            )}
          </button>

          <button
            onClick={() => handleMerge('target')}
            disabled={!canMerge || loading !== null}
            className="w-full px-3 py-2 text-left text-sm hover:bg-surface disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-between"
          >
            <span>Merge to USDC</span>
            {loading?.startsWith('merge') && (
              <span className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
            )}
          </button>

          <div className="border-t border-border my-1" />

          <a
            href={`https://polymarket.com/event/${p.target_group_slug}`}
            target="_blank"
            rel="noopener noreferrer"
            className="w-full px-3 py-2 text-left text-sm hover:bg-surface flex items-center justify-between text-text-secondary"
          >
            <span>View on Polymarket</span>
            <span>↗</span>
          </a>
        </div>
      )}
    </div>
  )
}
