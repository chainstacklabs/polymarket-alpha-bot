'use client'

import { useState, useEffect } from 'react'

export type Density = 'compact' | 'comfortable'

const STORAGE_KEY = 'alphapoly:density'

export function useDensity() {
  const [density, setDensity] = useState<Density>('comfortable')
  const [mounted, setMounted] = useState(false)

  // Load from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY) as Density | null
    if (stored === 'compact' || stored === 'comfortable') {
      setDensity(stored)
    }
    setMounted(true)
  }, [])

  // Persist to localStorage
  const updateDensity = (newDensity: Density) => {
    setDensity(newDensity)
    localStorage.setItem(STORAGE_KEY, newDensity)
  }

  const toggle = () => {
    updateDensity(density === 'compact' ? 'comfortable' : 'compact')
  }

  return { density, setDensity: updateDensity, toggle, mounted }
}

interface DensityToggleProps {
  density: Density
  onToggle: () => void
}

export function DensityToggle({ density, onToggle }: DensityToggleProps) {
  return (
    <button
      onClick={onToggle}
      className="flex items-center gap-1.5 px-2 py-1 rounded border border-border bg-surface-elevated hover:bg-surface-hover text-text-muted hover:text-text-secondary transition-colors"
      title={`Switch to ${density === 'compact' ? 'comfortable' : 'compact'} view`}
    >
      {density === 'compact' ? (
        // Compact icon (tight lines)
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
        </svg>
      ) : (
        // Comfortable icon (spaced lines)
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 5h16M4 12h16M4 19h16" />
        </svg>
      )}
      <span className="text-xs">{density === 'compact' ? 'Compact' : 'Comfortable'}</span>
    </button>
  )
}

// =============================================================================
// DENSITY STYLES
// =============================================================================

export const densityStyles = {
  compact: {
    rowPadding: 'py-1.5',
    cellPadding: 'px-2 py-1.5',
    fontSize: 'text-xs',
    headerPadding: 'px-2 py-2',
  },
  comfortable: {
    rowPadding: 'py-2',
    cellPadding: 'px-2.5 py-2',
    fontSize: 'text-sm',
    headerPadding: 'px-2.5 py-2.5',
  },
} as const
