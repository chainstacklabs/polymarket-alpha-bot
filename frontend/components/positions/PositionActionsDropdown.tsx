'use client'

import { useState, useRef, useEffect } from 'react'
import { getApiBaseUrl } from '@/config/api-config'
import { getOrderSettings } from '@/components/OrderSettings'
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
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

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

  const hasTokens =
    p.target_balance > 0.01 ||
    p.target_unwanted_balance > 0.01 ||
    p.cover_balance > 0.01 ||
    p.cover_unwanted_balance > 0.01 ||
    (p.target_split_tx && !p.target_clob_order_id && !p.target_clob_filled) ||
    (p.cover_split_tx && !p.cover_clob_order_id && !p.cover_clob_filled)

  const handleExit = async () => {
    setLoading(true)
    setError(null)
    try {
      const settings = getOrderSettings()
      const res = await fetch(
        `${getApiBaseUrl()}/positions/${p.position_id}/exit`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slippage: settings.slippage }),
        }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Exit failed')
      setOpen(false)
      onRefresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setLoading(false)
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
            onClick={handleExit}
            disabled={!hasTokens || loading}
            className="w-full px-3 py-2 text-left text-sm hover:bg-surface disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-between"
          >
            <span>{loading ? 'Exiting...' : 'Exit Position'}</span>
            {loading && (
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
