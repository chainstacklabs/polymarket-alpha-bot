import { useState, useCallback, useMemo } from 'react'

const STORAGE_KEY = 'alphapoly:favorites'

export interface FavoriteEntry {
  pair_id: string
  added_at: number // timestamp when favorited
  coverage_at_add: number // coverage when added (to show change)
}

export function useFavorites() {
  const [favorites, setFavorites] = useState<Map<string, FavoriteEntry>>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored) {
        const parsed = JSON.parse(stored) as FavoriteEntry[]
        return new Map(parsed.map((f) => [f.pair_id, f]))
      }
    } catch (e) {
      console.debug('Failed to load favorites:', e)
    }
    return new Map()
  })

  // Persist to localStorage
  const persist = useCallback((newFavorites: Map<string, FavoriteEntry>) => {
    try {
      const array = Array.from(newFavorites.values())
      localStorage.setItem(STORAGE_KEY, JSON.stringify(array))
    } catch (e) {
      console.debug('Failed to persist favorites:', e)
    }
  }, [])

  // Add a favorite
  const addFavorite = useCallback(
    (pairId: string, coverage: number) => {
      setFavorites((prev) => {
        const next = new Map(prev)
        next.set(pairId, {
          pair_id: pairId,
          added_at: Date.now(),
          coverage_at_add: coverage,
        })
        persist(next)
        return next
      })
    },
    [persist]
  )

  // Remove a favorite
  const removeFavorite = useCallback(
    (pairId: string) => {
      setFavorites((prev) => {
        const next = new Map(prev)
        next.delete(pairId)
        persist(next)
        return next
      })
    },
    [persist]
  )

  // Toggle a favorite
  const toggleFavorite = useCallback(
    (pairId: string, coverage: number) => {
      if (favorites.has(pairId)) {
        removeFavorite(pairId)
      } else {
        addFavorite(pairId, coverage)
      }
    },
    [favorites, addFavorite, removeFavorite]
  )

  // Stable Set for O(1) lookups without causing re-renders
  const favoriteSet = useMemo(() => new Set(favorites.keys()), [favorites])

  // Check if favorited - uses the memoized Set
  const isFavorite = useCallback(
    (pairId: string) => favoriteSet.has(pairId),
    [favoriteSet]
  )

  // Clear all favorites
  const clearAll = useCallback(() => {
    setFavorites(new Map())
    localStorage.removeItem(STORAGE_KEY)
  }, [])

  // Get sorted favorite IDs (most recently added first) - memoized
  const favoriteIds = useMemo(
    () =>
      Array.from(favorites.values())
        .sort((a, b) => b.added_at - a.added_at)
        .map((f) => f.pair_id),
    [favorites]
  )

  return {
    favoriteIds,
    favoriteSet,
    toggleFavorite,
    isFavorite,
    clearAll,
    count: favorites.size,
  }
}
