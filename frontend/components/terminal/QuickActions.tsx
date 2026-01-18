'use client'

import { useState } from 'react'
import type { Portfolio } from '@/components/PortfolioModal'

interface QuickActionsProps {
  portfolio: Portfolio
  isFavorite: boolean
  onToggleFavorite: () => void
}

export function QuickActions({ portfolio: p, isFavorite, onToggleFavorite }: QuickActionsProps) {
  const [copied, setCopied] = useState(false)

  // Generate strategy summary for clipboard
  const generateSummary = () => {
    return `Strategy: ${p.target_question}
Target: ${p.target_position} @ $${p.target_price.toFixed(2)}
Backup: ${p.cover_question} (${p.cover_position} @ $${p.cover_price.toFixed(2)})
LLM Confidence: ${(p.coverage * 100).toFixed(1)}%
Cost: $${p.total_cost.toFixed(2)}
Expected Return: ${(p.expected_profit * 100).toFixed(1)}%`
  }

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await navigator.clipboard.writeText(generateSummary())
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy:', err)
    }
  }

  const handleOpenTarget = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (p.target_group_slug) {
      window.open(`https://polymarket.com/event/${p.target_group_slug}`, '_blank')
    }
  }

  const handleOpenCover = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (p.cover_group_slug) {
      window.open(`https://polymarket.com/event/${p.cover_group_slug}`, '_blank')
    }
  }

  const handleToggleFavorite = (e: React.MouseEvent) => {
    e.stopPropagation()
    onToggleFavorite()
  }

  return (
    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
      {/* Favorite button */}
      <button
        onClick={handleToggleFavorite}
        className={`p-1 rounded transition-colors ${
          isFavorite
            ? 'text-amber hover:text-amber/80'
            : 'text-text-muted hover:text-amber'
        }`}
        title={isFavorite ? 'Remove from favorites' : 'Add to favorites'}
      >
        <svg
          className="w-3.5 h-3.5"
          fill={isFavorite ? 'currentColor' : 'none'}
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z"
          />
        </svg>
      </button>

      {/* Copy button */}
      <button
        onClick={handleCopy}
        className="p-1 rounded text-text-muted hover:text-text-primary transition-colors"
        title="Copy strategy summary"
      >
        {copied ? (
          <svg className="w-3.5 h-3.5 text-emerald" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        ) : (
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
            />
          </svg>
        )}
      </button>

      {/* Open target market */}
      {p.target_group_slug && (
        <button
          onClick={handleOpenTarget}
          className="p-1 rounded text-text-muted hover:text-cyan transition-colors"
          title="Open target market on Polymarket"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
            />
          </svg>
        </button>
      )}
    </div>
  )
}

// =============================================================================
// FAVORITE STAR BUTTON (standalone for table cells)
// =============================================================================

interface FavoriteButtonProps {
  isFavorite: boolean
  onToggle: () => void
  size?: 'sm' | 'md'
}

export function FavoriteButton({ isFavorite, onToggle, size = 'sm' }: FavoriteButtonProps) {
  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    onToggle()
  }

  const sizeClass = size === 'sm' ? 'w-4 h-4' : 'w-5 h-5'

  return (
    <button
      onClick={handleClick}
      className={`p-0.5 rounded transition-colors ${
        isFavorite
          ? 'text-amber hover:text-amber/80'
          : 'text-text-muted/50 hover:text-amber'
      }`}
      title={isFavorite ? 'Remove from watchlist' : 'Add to watchlist'}
    >
      <svg
        className={sizeClass}
        fill={isFavorite ? 'currentColor' : 'none'}
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M11.049 2.927c.3-.921 1.603-.921 1.902 0l1.519 4.674a1 1 0 00.95.69h4.915c.969 0 1.371 1.24.588 1.81l-3.976 2.888a1 1 0 00-.363 1.118l1.518 4.674c.3.922-.755 1.688-1.538 1.118l-3.976-2.888a1 1 0 00-1.176 0l-3.976 2.888c-.783.57-1.838-.197-1.538-1.118l1.518-4.674a1 1 0 00-.363-1.118l-3.976-2.888c-.784-.57-.38-1.81.588-1.81h4.914a1 1 0 00.951-.69l1.519-4.674z"
        />
      </svg>
    </button>
  )
}
