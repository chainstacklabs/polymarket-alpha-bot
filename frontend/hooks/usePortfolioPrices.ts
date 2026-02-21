'use client'

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  startTransition,
} from 'react'
import type { Portfolio } from '@/types/portfolio'

// Re-export for consumers that import from this file
export type { Portfolio } from '@/types/portfolio'

// =============================================================================
// TYPES
// =============================================================================

interface PortfolioSummary {
  total: number
  by_tier: Record<string, number>
  profitable_count: number
  market_count?: number
}

interface FilterState {
  maxTier: number
  profitableOnly: boolean
}

type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error'

interface PriceChange {
  direction: 'up' | 'down'
  timestamp: number
}

export interface UsePortfolioPricesResult {
  portfolios: Portfolio[]
  summary: PortfolioSummary | null
  connected: boolean
  status: ConnectionStatus
  changedIds: Set<string>
  priceChanges: Map<string, PriceChange>
  updateFilters: (filters: FilterState) => void
  reconnect: () => void
}

import { getPortfolioWsUrl, getApiBaseUrl } from '@/config/api-config'

// =============================================================================
// CONSTANTS
// =============================================================================

const getWsUrl = () => getPortfolioWsUrl()
const RECONNECT_DELAY = 5000
const CHANGE_FLASH_DURATION = 2000
const POLLING_INTERVAL = 3000 // Fallback polling interval when WS fails
const WS_TIMEOUT = 5000 // Time to wait for WS before falling back to polling

// =============================================================================
// HOOK
// =============================================================================

export function usePortfolioPrices(
  initialFilters: FilterState = { maxTier: 2, profitableOnly: false }
): UsePortfolioPricesResult {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([])
  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const [changedIds, setChangedIds] = useState<Set<string>>(new Set())
  const [priceChanges, setPriceChanges] = useState<Map<string, PriceChange>>(
    new Map()
  )

  const wsRef = useRef<WebSocket | null>(null)
  const filtersRef = useRef<FilterState>(initialFilters)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | undefined>(undefined)
  const wsTimeoutRef = useRef<NodeJS.Timeout | undefined>(undefined) // Timeout for WS connection
  const pollingIntervalRef = useRef<NodeJS.Timeout | undefined>(undefined)
  const portfolioMapRef = useRef<Map<string, Portfolio>>(new Map())
  const mountedRef = useRef(true) // Track if component is mounted
  const wsFailedRef = useRef(false) // Track if WS has failed (use polling instead)
  const statusRef = useRef<ConnectionStatus>(status) // Ref for stable callback identity

  // Keep statusRef in sync with status state
  useEffect(() => {
    statusRef.current = status
  }, [status])

  // REST API fallback for when WebSocket fails
  // NOTE: Uses statusRef for stable callback identity - prevents cascading re-renders
  // that were causing the table to reload unnecessarily when connection status changed
  const fetchViaRest = useCallback(async () => {
    if (!mountedRef.current) return

    try {
      const filters = filtersRef.current
      const params = new URLSearchParams({
        max_tier: String(filters.maxTier),
        ...(filters.profitableOnly && { profitable_only: 'true' }),
      })
      const res = await fetch(`${getApiBaseUrl()}/data/portfolios?${params}`)
      if (!res.ok) return

      const data = await res.json()
      // API returns { data: { portfolios: [...] }, ... }
      const fetchedPortfolios = (data.data?.portfolios ||
        data.portfolios ||
        []) as Portfolio[]

      // Update state
      portfolioMapRef.current = new Map(
        fetchedPortfolios.map((p) => [p.pair_id, p])
      )
      setPortfolios(fetchedPortfolios)
      if (data.summary) setSummary(data.summary)

      // Mark as connected (via polling)
      if (statusRef.current !== 'connected') {
        setStatus('connected')
      }
    } catch (e) {
      console.error('REST fetch failed:', e)
    }
  }, []) // No dependencies - uses refs for stable identity

  // Start polling when WebSocket fails
  const startPolling = useCallback(() => {
    if (pollingIntervalRef.current) return // Already polling

    console.log('Starting REST polling fallback')
    fetchViaRest() // Initial fetch
    pollingIntervalRef.current = setInterval(fetchViaRest, POLLING_INTERVAL)
  }, [fetchViaRest])

  // Stop polling
  const stopPolling = useCallback(() => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current)
      pollingIntervalRef.current = undefined
    }
  }, [])

  // Clear old changes - use startTransition for non-urgent visual updates
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now()
      const activeIds = new Set<string>()

      // Wrap in startTransition since flash effects are non-urgent
      startTransition(() => {
        setPriceChanges((prev) => {
          const filtered = new Map<string, PriceChange>()
          prev.forEach((change, id) => {
            if (now - change.timestamp < CHANGE_FLASH_DURATION) {
              filtered.set(id, change)
              activeIds.add(id)
            }
          })
          return filtered.size !== prev.size ? filtered : prev
        })

        setChangedIds((prev) => {
          if (prev.size === 0) return prev
          // Only keep IDs that still have active price changes
          const stillActive = new Set(
            [...prev].filter((id) => activeIds.has(id))
          )
          return stillActive.size !== prev.size ? stillActive : prev
        })
      })
    }, 500)

    return () => clearInterval(interval)
  }, [])

  const connect = useCallback(() => {
    // Don't connect if unmounted
    if (!mountedRef.current) {
      return
    }

    // Already connected or connecting - don't create duplicate connections
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return
    }

    // Close any stale connection (CLOSING state)
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.onerror = null
      wsRef.current.onmessage = null
      wsRef.current.onopen = null
      wsRef.current.close()
      wsRef.current = null
    }

    setStatus('connecting')

    try {
      const ws = new WebSocket(getWsUrl())
      wsRef.current = ws

      ws.onopen = () => {
        if (!mountedRef.current) {
          ws.close()
          return
        }
        setStatus('connected')
        wsFailedRef.current = false
        stopPolling() // Stop polling if WS connects

        // Send initial filters immediately so server uses correct filters for 'initial' message
        const filters = filtersRef.current
        ws.send(
          JSON.stringify({
            type: 'filter',
            max_tier: filters.maxTier,
            profitable_only: filters.profitableOnly,
          })
        )
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)

          switch (data.type) {
            case 'connected':
              // Connection confirmed
              break

            case 'initial':
              // Initial load of portfolios
              const initialPortfolios = data.portfolios as Portfolio[]
              portfolioMapRef.current = new Map(
                initialPortfolios.map((p) => [p.pair_id, p])
              )
              setPortfolios(initialPortfolios)
              setSummary(data.summary)
              break

            case 'portfolio_update':
              // Delta update from price changes
              const changed = data.changed as Portfolio[]
              const removed = (data.removed || []) as string[]

              // Update summary if provided (for real-time stats)
              if (data.summary) {
                setSummary(data.summary)
              }

              // Remove portfolios that no longer match filters
              if (removed.length > 0) {
                removed.forEach((id) => portfolioMapRef.current.delete(id))
              }

              if (changed.length > 0 || removed.length > 0) {
                // Track changes for flash effect
                const newChangedIds = new Set<string>()
                const newPriceChanges = new Map<string, PriceChange>()

                changed.forEach((updated) => {
                  const old = portfolioMapRef.current.get(updated.pair_id)
                  if (old) {
                    // Determine price direction
                    const oldProfit = old.expected_profit
                    const newProfit = updated.expected_profit
                    if (Math.abs(newProfit - oldProfit) > 0.001) {
                      newPriceChanges.set(updated.pair_id, {
                        direction: newProfit > oldProfit ? 'up' : 'down',
                        timestamp: Date.now(),
                      })
                    }
                  }
                  newChangedIds.add(updated.pair_id)
                  portfolioMapRef.current.set(updated.pair_id, updated)
                })

                // Merge and re-sort
                const merged = Array.from(portfolioMapRef.current.values())
                merged.sort((a, b) =>
                  a.tier !== b.tier ? a.tier - b.tier : b.coverage - a.coverage
                )

                // Portfolio data update is urgent
                setPortfolios(merged)

                // Flash effects are non-urgent visual updates
                startTransition(() => {
                  setChangedIds((prev) => new Set([...prev, ...newChangedIds]))
                  setPriceChanges(
                    (prev) => new Map([...prev, ...newPriceChanges])
                  )
                })
              }
              break

            case 'filter_ack':
              // Filter change acknowledged, replace portfolio list
              const filteredPortfolios = data.portfolios as Portfolio[]
              portfolioMapRef.current = new Map(
                filteredPortfolios.map((p) => [p.pair_id, p])
              )
              setPortfolios(filteredPortfolios)
              break

            case 'full_reload':
              // Server signaling data was reset/changed, replace all portfolios
              const reloadedPortfolios = data.portfolios as Portfolio[]
              portfolioMapRef.current = new Map(
                reloadedPortfolios.map((p) => [p.pair_id, p])
              )
              setPortfolios(reloadedPortfolios)
              setSummary(data.summary)
              // Clear any pending changes
              setChangedIds(new Set())
              setPriceChanges(new Map())
              break

            case 'heartbeat':
              // Keep-alive, nothing to do
              break
          }
        } catch (e) {
          console.error('Failed to parse WebSocket message:', e)
        }
      }

      ws.onclose = () => {
        // Ignore if this WebSocket is stale (replaced by a new connection)
        if (wsRef.current !== ws) {
          return
        }
        wsRef.current = null

        // If WS has failed before, use polling instead of reconnecting
        if (wsFailedRef.current) {
          setStatus('connected') // Show as connected (via polling)
          startPolling()
          return
        }

        setStatus('disconnected')

        // Reconnect after delay (only if still mounted)
        if (mountedRef.current) {
          reconnectTimeoutRef.current = setTimeout(() => {
            console.log('Attempting to reconnect...')
            connect()
          }, RECONNECT_DELAY)
        }
      }

      ws.onerror = () => {
        // Ignore if this WebSocket is stale (replaced by a new connection)
        if (wsRef.current !== ws) {
          return
        }

        console.error(
          'Portfolio WebSocket error - falling back to REST polling'
        )
        wsFailedRef.current = true
        startPolling() // Start REST polling as fallback
        ws.close()
      }
      // Set a timeout - if WS doesn't connect in time, fall back to polling
      // Clear any existing timeout first
      if (wsTimeoutRef.current) {
        clearTimeout(wsTimeoutRef.current)
      }
      wsTimeoutRef.current = setTimeout(() => {
        // Only trigger if this is still the current WebSocket and component is mounted
        if (
          wsRef.current === ws &&
          mountedRef.current &&
          ws.readyState !== WebSocket.OPEN &&
          !wsFailedRef.current
        ) {
          console.log(
            'WebSocket connection timeout - falling back to REST polling'
          )
          wsFailedRef.current = true
          startPolling()
        }
      }, WS_TIMEOUT)
    } catch (e) {
      console.error('Failed to connect WebSocket:', e)
      wsFailedRef.current = true
      startPolling() // Fall back to polling
    }
  }, [startPolling, stopPolling])

  // Initial connection - only run once on mount
  useEffect(() => {
    mountedRef.current = true
    wsFailedRef.current = false

    // Use the connect callback for initial connection
    connect()

    // Cleanup
    return () => {
      mountedRef.current = false
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current)
      if (wsTimeoutRef.current) clearTimeout(wsTimeoutRef.current)
      if (pollingIntervalRef.current) clearInterval(pollingIntervalRef.current)

      // Close WebSocket if it exists
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.onerror = null
        wsRef.current.onmessage = null
        wsRef.current.onopen = null
        wsRef.current.close()
        wsRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Send filter updates to server
  // Only fetch via REST if WebSocket is not connected, otherwise use WS
  // This prevents double portfolio replacement (REST + filter_ack)
  const updateFilters = useCallback(
    (filters: FilterState) => {
      filtersRef.current = filters

      // If WebSocket is connected, use it (will get filter_ack with portfolios)
      // Otherwise fall back to REST
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({
            type: 'filter',
            max_tier: filters.maxTier,
            profitable_only: filters.profitableOnly,
          })
        )
      } else {
        fetchViaRest()
      }
    },
    [fetchViaRest]
  )

  // Manual reconnect
  const reconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close()
    }
    connect()
  }, [connect])

  return {
    portfolios,
    summary,
    connected: status === 'connected',
    status,
    changedIds,
    priceChanges,
    updateFilters,
    reconnect,
  }
}
