'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

// =============================================================================
// TYPES
// =============================================================================

export interface Portfolio {
  pair_id: string
  // Target
  target_group_id: string
  target_group_title: string
  target_group_slug?: string
  target_market_id: string
  target_question: string
  target_position: 'YES' | 'NO'
  target_price: number
  target_bracket?: string
  // Cover
  cover_group_id: string
  cover_group_title: string
  cover_group_slug?: string
  cover_market_id: string
  cover_question: string
  cover_position: 'YES' | 'NO'
  cover_price: number
  cover_bracket?: string
  cover_probability: number
  // Relationship
  relationship: string
  relationship_type: string
  // Metrics
  total_cost: number
  profit: number
  profit_pct: number
  coverage: number
  loss_probability: number
  expected_profit: number
  // Tier
  tier: number
  tier_label: string
  // Validation
  viability_score?: number
  validation_analysis?: string
}

interface TierChange {
  pair_id: string
  old_tier: number
  new_tier: number
  coverage: number
}

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
  tierChanges: TierChange[]
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
const POLLING_INTERVAL = 3000  // Fallback polling interval when WS fails
const WS_TIMEOUT = 5000  // Time to wait for WS before falling back to polling

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
  const [priceChanges, setPriceChanges] = useState<Map<string, PriceChange>>(new Map())
  const [tierChanges, setTierChanges] = useState<TierChange[]>([])

  const wsRef = useRef<WebSocket | null>(null)
  const filtersRef = useRef<FilterState>(initialFilters)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout>()
  const pollingIntervalRef = useRef<NodeJS.Timeout>()
  const portfolioMapRef = useRef<Map<string, Portfolio>>(new Map())
  const mountedRef = useRef(true)  // Track if component is mounted
  const wsFailedRef = useRef(false)  // Track if WS has failed (use polling instead)

  // REST API fallback for when WebSocket fails
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
      const fetchedPortfolios = (data.data?.portfolios || data.portfolios || []) as Portfolio[]

      // Update state
      portfolioMapRef.current = new Map(
        fetchedPortfolios.map(p => [p.pair_id, p])
      )
      setPortfolios(fetchedPortfolios)
      if (data.summary) setSummary(data.summary)

      // Mark as connected (via polling)
      if (status !== 'connected') {
        setStatus('connected')
      }
    } catch (e) {
      console.error('REST fetch failed:', e)
    }
  }, [status])

  // Start polling when WebSocket fails
  const startPolling = useCallback(() => {
    if (pollingIntervalRef.current) return  // Already polling

    console.log('Starting REST polling fallback')
    fetchViaRest()  // Initial fetch
    pollingIntervalRef.current = setInterval(fetchViaRest, POLLING_INTERVAL)
  }, [fetchViaRest])

  // Stop polling
  const stopPolling = useCallback(() => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current)
      pollingIntervalRef.current = undefined
    }
  }, [])

  // Clear old changes
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now()
      setPriceChanges(prev => {
        const filtered = new Map<string, PriceChange>()
        prev.forEach((change, id) => {
          if (now - change.timestamp < CHANGE_FLASH_DURATION) {
            filtered.set(id, change)
          }
        })
        return filtered.size !== prev.size ? filtered : prev
      })

      setChangedIds(prev => {
        if (prev.size === 0) return prev
        return new Set()
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
    if (wsRef.current?.readyState === WebSocket.OPEN ||
        wsRef.current?.readyState === WebSocket.CONNECTING) {
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
        console.log('Portfolio WebSocket connected')
        setStatus('connected')
        wsFailedRef.current = false
        stopPolling()  // Stop polling if WS connects
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
                initialPortfolios.map(p => [p.pair_id, p])
              )
              setPortfolios(initialPortfolios)
              setSummary(data.summary)
              break

            case 'portfolio_update':
              // Delta update from price changes
              const changed = data.changed as Portfolio[]
              const newTierChanges = data.tier_changes as TierChange[]

              if (changed.length > 0) {
                // Track changes for flash effect
                const newChangedIds = new Set<string>()
                const newPriceChanges = new Map<string, PriceChange>()

                changed.forEach(updated => {
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

                setPortfolios(merged)
                setChangedIds(prev => new Set([...prev, ...newChangedIds]))
                setPriceChanges(prev => new Map([...prev, ...newPriceChanges]))
              }

              if (newTierChanges.length > 0) {
                setTierChanges(newTierChanges)
                // Clear tier changes after a short delay
                setTimeout(() => setTierChanges([]), 3000)
              }
              break

            case 'filter_ack':
              // Filter change acknowledged, replace portfolio list
              const filteredPortfolios = data.portfolios as Portfolio[]
              portfolioMapRef.current = new Map(
                filteredPortfolios.map(p => [p.pair_id, p])
              )
              setPortfolios(filteredPortfolios)
              break

            case 'full_reload':
              // Server signaling data was reset/changed, replace all portfolios
              console.log('Portfolio data reloaded from server')
              const reloadedPortfolios = data.portfolios as Portfolio[]
              portfolioMapRef.current = new Map(
                reloadedPortfolios.map(p => [p.pair_id, p])
              )
              setPortfolios(reloadedPortfolios)
              setSummary(data.summary)
              // Clear any pending changes
              setChangedIds(new Set())
              setPriceChanges(new Map())
              setTierChanges([])
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

        console.log('Portfolio WebSocket disconnected')
        wsRef.current = null

        // If WS has failed before, use polling instead of reconnecting
        if (wsFailedRef.current) {
          setStatus('connected')  // Show as connected (via polling)
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

        console.error('Portfolio WebSocket error - falling back to REST polling')
        wsFailedRef.current = true
        startPolling()  // Start REST polling as fallback
        ws.close()
      }
      // Set a timeout - if WS doesn't connect in time, fall back to polling
      setTimeout(() => {
        if (ws.readyState !== WebSocket.OPEN && !wsFailedRef.current) {
          console.log('WebSocket connection timeout - falling back to REST polling')
          wsFailedRef.current = true
          startPolling()
        }
      }, WS_TIMEOUT)

    } catch (e) {
      console.error('Failed to connect WebSocket:', e)
      wsFailedRef.current = true
      startPolling()  // Fall back to polling
    }
  }, [startPolling, stopPolling])

  // Initial connection - only run once on mount
  useEffect(() => {
    mountedRef.current = true
    connect()

    return () => {
      mountedRef.current = false  // Mark as unmounted to prevent reconnect
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current)
      }
      if (wsRef.current) {
        // Remove handlers before closing to prevent error events
        wsRef.current.onclose = null
        wsRef.current.onerror = null
        wsRef.current.onmessage = null
        wsRef.current.onopen = null
        wsRef.current.close()
        wsRef.current = null
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])  // Empty deps - connect is stable and doesn't need to trigger re-runs

  // Send filter updates to server
  const updateFilters = useCallback((filters: FilterState) => {
    filtersRef.current = filters

    // Always fetch via REST immediately for responsive UI
    fetchViaRest()

    // Also send via WebSocket if connected (for real-time updates)
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'filter',
        max_tier: filters.maxTier,
        profitable_only: filters.profitableOnly,
      }))
    }
  }, [fetchViaRest])

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
    tierChanges,
    updateFilters,
    reconnect,
  }
}
