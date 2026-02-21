'use client'

import {
  useState,
  useEffect,
  useCallback,
  createContext,
  useContext,
  ReactNode,
} from 'react'
import { getApiBaseUrl } from '@/config/api-config'

interface WalletBalances {
  pol: number
  usdc_e: number
}

interface WalletStatus {
  exists: boolean
  address: string | null
  unlocked: boolean
  balances: WalletBalances | null
  approvals_set: boolean
}

interface WalletContextValue {
  status: WalletStatus | null
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  generate: (password: string) => Promise<string>
  importKey: (privateKey: string, password: string) => Promise<string>
  unlock: (password: string) => Promise<void>
  lock: () => Promise<void>
  approveContracts: () => Promise<void>
}

const WalletContext = createContext<WalletContextValue | null>(null)

export function WalletProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<WalletStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const apiBase = getApiBaseUrl()

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/wallet/status`)
      if (!res.ok) throw new Error('Failed to fetch wallet status')
      const data = await res.json()
      setStatus(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [apiBase])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 30000)
    return () => clearInterval(interval)
  }, [refresh])

  const generate = useCallback(
    async (password: string): Promise<string> => {
      const res = await fetch(`${apiBase}/wallet/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Failed to generate wallet')
      }
      const data = await res.json()
      await refresh()
      return data.address
    },
    [apiBase, refresh]
  )

  const importKey = useCallback(
    async (privateKey: string, password: string): Promise<string> => {
      const res = await fetch(`${apiBase}/wallet/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ private_key: privateKey, password }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Failed to import wallet')
      }
      const data = await res.json()
      await refresh()
      return data.address
    },
    [apiBase, refresh]
  )

  const unlock = useCallback(
    async (password: string): Promise<void> => {
      const res = await fetch(`${apiBase}/wallet/unlock`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail || 'Invalid password')
      }
      const data = await res.json()
      // Optimistic update - show unlocked immediately, refresh balances in background
      setStatus((prev) =>
        prev ? { ...prev, unlocked: true, address: data.address } : null
      )
      refresh()
    },
    [apiBase, refresh]
  )

  const lock = useCallback(async (): Promise<void> => {
    await fetch(`${apiBase}/wallet/lock`, { method: 'POST' })
    // Optimistic update - don't wait for slow RPC calls in refresh()
    setStatus((prev) =>
      prev ? { ...prev, unlocked: false, balances: null } : null
    )
  }, [apiBase])

  const approveContracts = useCallback(async (): Promise<void> => {
    const res = await fetch(`${apiBase}/wallet/approve-contracts`, {
      method: 'POST',
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Failed to approve contracts')
    }
    await refresh()
  }, [apiBase, refresh])

  return (
    <WalletContext.Provider
      value={{
        status,
        loading,
        error,
        refresh,
        generate,
        importKey,
        unlock,
        lock,
        approveContracts,
      }}
    >
      {children}
    </WalletContext.Provider>
  )
}

export function useWallet(): WalletContextValue {
  const context = useContext(WalletContext)
  if (!context) {
    throw new Error('useWallet must be used within a WalletProvider')
  }
  return context
}
