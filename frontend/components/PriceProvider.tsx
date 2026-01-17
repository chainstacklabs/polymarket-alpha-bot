'use client'

import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { getPricesWsUrl } from '@/config/api-config'

interface PriceData {
  price: number
  title?: string
  market_id?: string
}

interface PriceContextType {
  prices: Record<string, PriceData>
  connected: boolean
}

const PriceContext = createContext<PriceContextType>({
  prices: {},
  connected: false,
})

export function usePriceContext() {
  return useContext(PriceContext)
}

export function PriceProvider({ children }: { children: ReactNode }) {
  const [prices, setPrices] = useState<Record<string, PriceData>>({})
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimeout: NodeJS.Timeout

    function connect() {
      try {
        ws = new WebSocket(getPricesWsUrl())

        ws.onopen = () => {
          console.log('WebSocket connected')
          setConnected(true)
        }

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)
            if (data.type === 'price_update') {
              setPrices(data.prices)
            }
          } catch (e) {
            console.error('Failed to parse WebSocket message:', e)
          }
        }

        ws.onclose = () => {
          console.log('WebSocket disconnected')
          setConnected(false)
          // Reconnect after 5 seconds
          reconnectTimeout = setTimeout(connect, 5000)
        }

        ws.onerror = (error) => {
          console.error('WebSocket error:', error)
          ws?.close()
        }
      } catch (e) {
        console.error('Failed to connect WebSocket:', e)
        setConnected(false)
        reconnectTimeout = setTimeout(connect, 5000)
      }
    }

    connect()

    return () => {
      clearTimeout(reconnectTimeout)
      ws?.close()
    }
  }, [])

  return (
    <PriceContext.Provider value={{ prices, connected }}>
      {children}
    </PriceContext.Provider>
  )
}
