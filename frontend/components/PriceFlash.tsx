'use client'

import { ReactNode, useEffect, useState } from 'react'

interface PriceFlashProps {
  /** Whether the flash effect is active */
  active: boolean
  /** Direction of price change (affects color) */
  direction?: 'up' | 'down' | null
  /** Content to wrap with flash effect */
  children: ReactNode
  /** Optional additional class names */
  className?: string
}

/**
 * Wraps content with a flash effect for price changes.
 *
 * - Green flash for price increases (direction='up')
 * - Red flash for price decreases (direction='down')
 * - Cyan flash for generic changes (direction=null)
 */
export function PriceFlash({
  active,
  direction,
  children,
  className = '',
}: PriceFlashProps) {
  const [isFlashing, setIsFlashing] = useState(false)
  const [flashDirection, setFlashDirection] = useState<'up' | 'down' | null>(null)

  useEffect(() => {
    if (active) {
      setIsFlashing(true)
      setFlashDirection(direction ?? null)

      // Clear flash after animation
      const timeout = setTimeout(() => {
        setIsFlashing(false)
        setFlashDirection(null)
      }, 2000)

      return () => clearTimeout(timeout)
    }
  }, [active, direction])

  const flashClass = isFlashing
    ? flashDirection === 'up'
      ? 'animate-flash-up'
      : flashDirection === 'down'
        ? 'animate-flash-down'
        : 'animate-flash'
    : ''

  return (
    <div className={`${flashClass} ${className}`}>
      {children}
    </div>
  )
}

/**
 * Arrow indicator for price changes.
 * Shows up/down arrow with color coding.
 */
export function PriceChangeIndicator({
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
}

/**
 * Badge showing tier change notification.
 */
export function TierChangeBadge({
  oldTier,
  newTier,
  className = '',
}: {
  oldTier: number
  newTier: number
  className?: string
}) {
  const improved = newTier < oldTier
  const tierLabels: Record<number, string> = {
    1: 'Excellent',
    2: 'Good',
    3: 'Fair',
    4: 'Low',
  }

  return (
    <span
      className={`
        inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium
        ${improved ? 'bg-emerald/20 text-emerald' : 'bg-rose/20 text-rose'}
        animate-pulse
        ${className}
      `}
    >
      {improved ? '↑' : '↓'}
      {tierLabels[oldTier]} → {tierLabels[newTier]}
    </span>
  )
}
