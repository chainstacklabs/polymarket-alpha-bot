'use client'

import { useState, useSyncExternalStore } from 'react'
import { getApiBaseUrl } from '@/config/api-config'
import {
  getOrderSettings,
  subscribeSettings,
  getSettingsSnapshot,
  getSettingsServerSnapshot,
} from '@/components/OrderSettings'
import type { Position } from '@/app/positions/page'

interface PositionExpandedDetailsProps {
  position: Position
  onRefresh: () => void
}

interface ExitResult {
  success: boolean
  message: string
  total: number
}

function getSideStatus(
  filled: boolean,
  orderId: string | null,
  unwantedBalance: number
): { label: string; color: string } {
  if (unwantedBalance >= 0.01) {
    if (orderId && !filled) return { label: 'PENDING', color: 'text-amber' }
    return { label: 'NEEDS SELL', color: 'text-rose' }
  }
  if (!orderId && !filled) return { label: 'NEEDS SELL', color: 'text-rose' }
  return { label: 'RECOVERED', color: 'text-emerald' }
}

function formatTxHash(hash: string): string {
  if (!hash) return ''
  return hash.startsWith('0x') ? hash : `0x${hash}`
}

/** Estimate what the exit will recover per side. */
function estimateSideExit(
  wanted: number,
  unwanted: number,
  price: number,
  slippage: number
) {
  const mergeable = Math.min(wanted, unwanted)
  const excessWanted = wanted - mergeable
  const excessUnwanted = unwanted - mergeable
  const slippageMul = 1 - slippage / 100

  const mergeValue = mergeable // $1.00 per merged pair
  const sellWantedValue = excessWanted * price * slippageMul
  const sellUnwantedValue = excessUnwanted * (1 - price) * slippageMul

  return {
    mergeable,
    excessWanted,
    excessUnwanted,
    mergeValue,
    sellValue: sellWantedValue + sellUnwantedValue,
    total: mergeValue + sellWantedValue + sellUnwantedValue,
  }
}

export function PositionExpandedDetails({
  position: p,
  onRefresh,
}: PositionExpandedDetailsProps) {
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ExitResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const targetStatus = getSideStatus(
    p.target_clob_filled,
    p.target_clob_order_id,
    p.target_unwanted_balance
  )
  const coverStatus = getSideStatus(
    p.cover_clob_filled,
    p.cover_clob_order_id,
    p.cover_unwanted_balance
  )

  const rawSettings = useSyncExternalStore(
    subscribeSettings,
    getSettingsSnapshot,
    getSettingsServerSnapshot
  )
  const settings = rawSettings ? getOrderSettings() : { slippage: 10 }

  const targetEst = estimateSideExit(
    p.target_balance,
    p.target_unwanted_balance,
    p.target_current_price,
    settings.slippage
  )
  const coverEst = estimateSideExit(
    p.cover_balance,
    p.cover_unwanted_balance,
    p.cover_current_price,
    settings.slippage
  )
  const totalEstimate = targetEst.total + coverEst.total
  const totalMerge = targetEst.mergeValue + coverEst.mergeValue
  const totalSell = targetEst.sellValue + coverEst.sellValue

  const hasTokens =
    p.target_balance > 0.01 ||
    p.target_unwanted_balance > 0.01 ||
    p.cover_balance > 0.01 ||
    p.cover_unwanted_balance > 0.01 ||
    (p.target_split_tx && !p.target_clob_order_id && !p.target_clob_filled) ||
    (p.cover_split_tx && !p.cover_clob_order_id && !p.cover_clob_filled)

  const handleExit = async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await fetch(
        `${getApiBaseUrl()}/positions/${p.position_id}/exit`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slippage: settings.slippage }),
        }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Exit failed')
      setResult({
        success: data.success,
        message: data.message,
        total: data.total_recovered,
      })
      onRefresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async () => {
    if (!confirm('Remove from list? Your tokens are NOT affected.')) return
    setDeleting(true)
    try {
      await fetch(`${getApiBaseUrl()}/positions/${p.position_id}`, {
        method: 'DELETE',
      })
      onRefresh()
    } catch (e) {
      console.error(e)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="bg-surface-elevated border-t border-border">
      {/* Banners */}
      {error && (
        <div className="mx-3 mt-2 px-2 py-1.5 bg-rose/10 border border-rose/25 rounded text-rose text-xs font-mono">
          {error}
        </div>
      )}
      {result && (
        <div
          className={`mx-3 mt-2 px-2 py-1.5 rounded text-xs font-mono ${result.success ? 'bg-emerald/10 border border-emerald/25 text-emerald' : 'bg-amber/10 border border-amber/25 text-amber'}`}
        >
          {result.message}
          {result.total > 0 && ` — recovered $${result.total.toFixed(2)}`}
        </div>
      )}

      {/* Two-column leg details */}
      <div className="grid grid-cols-2 gap-0 divide-x divide-border">
        {/* Target leg */}
        <div className="px-3 py-2.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[10px] text-text-muted uppercase tracking-wider">
              Target
            </span>
            <span className={`text-[10px] font-mono ${targetStatus.color}`}>
              {targetStatus.label}
            </span>
          </div>
          <p
            className="text-xs text-text-primary truncate mb-1.5"
            title={p.target_question}
          >
            {p.target_question}
          </p>
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
            <span className="text-text-muted">Side</span>
            <span
              className={`font-mono ${p.target_position === 'YES' ? 'text-emerald' : 'text-rose'}`}
            >
              {p.target_position}
            </span>

            <span className="text-text-muted">Bal</span>
            <span className="font-mono text-text-secondary">
              {p.target_balance.toFixed(2)}
              {p.target_unwanted_balance > 0.01 && (
                <span className="text-text-muted">
                  {' '}
                  + {p.target_unwanted_balance.toFixed(2)} unw
                </span>
              )}
            </span>

            <span className="text-text-muted">Price</span>
            <span className="font-mono text-text-secondary">
              ${p.target_entry_price.toFixed(3)}
              <span className="text-text-muted"> → </span>$
              {p.target_current_price.toFixed(3)}
            </span>
          </div>
        </div>

        {/* Cover leg */}
        <div className="px-3 py-2.5">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[10px] text-text-muted uppercase tracking-wider">
              Cover
            </span>
            <span className={`text-[10px] font-mono ${coverStatus.color}`}>
              {coverStatus.label}
            </span>
          </div>
          <p
            className="text-xs text-text-primary truncate mb-1.5"
            title={p.cover_question}
          >
            {p.cover_question}
          </p>
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px]">
            <span className="text-text-muted">Side</span>
            <span
              className={`font-mono ${p.cover_position === 'YES' ? 'text-emerald' : 'text-rose'}`}
            >
              {p.cover_position}
            </span>

            <span className="text-text-muted">Bal</span>
            <span className="font-mono text-text-secondary">
              {p.cover_balance.toFixed(2)}
              {p.cover_unwanted_balance > 0.01 && (
                <span className="text-text-muted">
                  {' '}
                  + {p.cover_unwanted_balance.toFixed(2)} unw
                </span>
              )}
            </span>

            <span className="text-text-muted">Price</span>
            <span className="font-mono text-text-secondary">
              ${p.cover_entry_price.toFixed(3)}
              <span className="text-text-muted"> → </span>$
              {p.cover_current_price.toFixed(3)}
            </span>
          </div>
        </div>
      </div>

      {/* Exit bar */}
      {hasTokens && (
        <div className="mx-3 mb-2.5 flex items-center justify-between gap-3 px-2.5 py-2 bg-surface rounded border border-border">
          <div className="flex items-center gap-3 text-[11px] font-mono text-text-muted">
            {totalMerge > 0.01 && (
              <span>
                merge{' '}
                <span className="text-emerald">${totalMerge.toFixed(2)}</span>
              </span>
            )}
            {totalSell > 0.01 && (
              <span>
                sell{' '}
                <span className="text-text-secondary">
                  ~${totalSell.toFixed(2)}
                </span>
              </span>
            )}
          </div>
          <button
            onClick={handleExit}
            disabled={loading}
            className="px-3 py-1 text-xs font-mono font-medium bg-cyan/15 text-cyan hover:bg-cyan/25 rounded border border-cyan/30 disabled:opacity-50 flex items-center gap-1.5 shrink-0"
          >
            {loading && (
              <span className="w-2.5 h-2.5 border border-current border-t-transparent rounded-full animate-spin" />
            )}
            {loading ? 'Exiting...' : `Exit → $${totalEstimate.toFixed(2)}`}
          </button>
        </div>
      )}

      {/* Footer */}
      <div className="px-3 py-1.5 border-t border-border flex items-center justify-between text-[10px] text-text-muted">
        <div className="flex items-center gap-2 font-mono">
          <span>{new Date(p.entry_time).toLocaleString()}</span>
          {p.target_split_tx && (
            <a
              href={`https://polygonscan.com/tx/${formatTxHash(p.target_split_tx)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-cyan/70 hover:text-cyan"
            >
              t.tx
            </a>
          )}
          {p.cover_split_tx && (
            <a
              href={`https://polygonscan.com/tx/${formatTxHash(p.cover_split_tx)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-cyan/70 hover:text-cyan"
            >
              c.tx
            </a>
          )}
        </div>
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="text-text-muted hover:text-rose disabled:opacity-50 font-mono"
        >
          {deleting ? '...' : 'remove'}
        </button>
      </div>
    </div>
  )
}
