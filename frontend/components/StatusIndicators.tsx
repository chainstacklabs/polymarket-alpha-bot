'use client'

import { useEffect, useState } from 'react'
import { WalletDropdown } from '@/components/terminal/WalletDropdown'
import { PipelineDropdown } from '@/components/terminal/PipelineDropdown'
import { getApiBaseUrl } from '@/config/api-config'
import { formatTime } from '@/utils/format-time'

interface StatusIndicatorsProps {
  /** WebSocket connection state (true = connected) */
  connected?: boolean
  /** WebSocket connection status for display text */
  connectionStatus?: 'connecting' | 'connected' | 'disconnected' | 'error'
}

export function StatusIndicators({
  connected,
  connectionStatus,
}: StatusIndicatorsProps) {
  const [lastRunTime, setLastRunTime] = useState<string | null>(null)

  useEffect(() => {
    async function fetchPipelineStatus() {
      try {
        const res = await fetch(`${getApiBaseUrl()}/pipeline/status`)
        if (res.ok) {
          const data = await res.json()
          setLastRunTime(data?.production?.last_run?.completed_at || null)
        }
      } catch (error) {
        console.debug('Failed to fetch pipeline status:', error)
      }
    }

    fetchPipelineStatus()
    const interval = setInterval(fetchPipelineStatus, 30000)
    return () => clearInterval(interval)
  }, [])

  // Determine connection display
  const showConnection = connected !== undefined
  const connectionText =
    connectionStatus === 'connecting'
      ? 'Connecting...'
      : connected
        ? 'Live prices'
        : 'Offline'

  return (
    <div className="flex items-center gap-3">
      {lastRunTime && (
        <span
          className="text-xs text-text-muted cursor-help"
          title="When the pipeline last analyzed markets for arbitrage opportunities"
        >
          Markets scanned {formatTime(lastRunTime)}
        </span>
      )}

      {/* Connection status */}
      {showConnection && (
        <div className="flex items-center gap-1.5">
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              connected ? 'bg-emerald animate-pulse' : 'bg-text-muted'
            }`}
          />
          <span className="text-xs text-text-muted">{connectionText}</span>
        </div>
      )}

      {/* Wallet dropdown */}
      <WalletDropdown />

      {/* Pipeline dropdown */}
      <PipelineDropdown />
    </div>
  )
}
