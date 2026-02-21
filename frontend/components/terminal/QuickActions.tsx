'use client'

import { memo, useCallback } from 'react'

// =============================================================================
// FAVORITE STAR BUTTON (standalone for table cells)
// Memoized to prevent unnecessary re-renders in virtualized lists
// =============================================================================

interface FavoriteButtonProps {
  isFavorite: boolean
  onToggle: () => void
  size?: 'sm' | 'md'
}

export const FavoriteButton = memo(function FavoriteButton({
  isFavorite,
  onToggle,
  size = 'sm',
}: FavoriteButtonProps) {
  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation()
      onToggle()
    },
    [onToggle]
  )

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
})
