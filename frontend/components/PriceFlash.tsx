'use client'

import { memo } from 'react'

/**
 * Arrow indicator for price changes.
 * Shows up/down arrow with color coding.
 * Memoized to prevent unnecessary re-renders in virtualized lists.
 */
export const PriceChangeIndicator = memo(function PriceChangeIndicator({
  direction,
  className = '',
}: {
  direction: 'up' | 'down' | null
  className?: string
}) {
  if (!direction) return null

  return (
    <span
      className={`
        inline-flex items-center justify-center
        text-xs font-bold
        ${direction === 'up' ? 'text-emerald' : 'text-rose'}
        animate-pulse
        ${className}
      `}
    >
      {direction === 'up' ? '↑' : '↓'}
    </span>
  )
})
