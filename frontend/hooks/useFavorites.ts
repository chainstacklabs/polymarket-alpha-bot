import { useState, useEffect, useCallback } from 'react'

const STORAGE_KEY = 'alphapoly:favorites'

export interface FavoriteEntry {
  pair_id: string
  added_at: number // timestamp when favorited
  coverage_at_add: number // coverage when added (to show change)
}

export function useFavorites() {
  const [favorites, setFavorites] = useState<Map<string, FavoriteEntry>>(new Map())
  const [mounted, setMounted] = useState(false)

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY)
      if (stored) {
        const parsed = JSON.parse(stored) as FavoriteEntry[]
        setFavorites(new Map(parsed.map((f) => [f.pair_id, f])))
      }
    } catch (e) {
      console.debug('Failed to load favorites:', e)
    }
    setMounted(true)
  }, [])

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

  // Check if favorited
  const isFavorite = useCallback(
    (pairId: string) => {
      return favorites.has(pairId)
    },
    [favorites]
  )

  // Get favorite entry (for showing change since added)
  const getFavorite = useCallback(
    (pairId: string) => {
      return favorites.get(pairId)
    },
    [favorites]
  )

  // Clear all favorites
  const clearAll = useCallback(() => {
    setFavorites(new Map())
    localStorage.removeItem(STORAGE_KEY)
  }, [])

  // Get sorted favorite IDs (most recently added first)
  const favoriteIds = Array.from(favorites.values())
    .sort((a, b) => b.added_at - a.added_at)
    .map((f) => f.pair_id)

  return {
    favorites,
    favoriteIds,
    addFavorite,
    removeFavorite,
    toggleFavorite,
    isFavorite,
    getFavorite,
    clearAll,
    count: favorites.size,
    mounted,
  }
}
