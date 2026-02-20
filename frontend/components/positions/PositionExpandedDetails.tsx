'use client'

import { useState } from 'react'
import { getApiBaseUrl } from '@/config/api-config'
import type { Position } from '@/app/positions/page'

interface PositionExpandedDetailsProps {
  position: Position
  onRefresh: () => void
}

function getSideStatus(filled: boolean, orderId: string | null, unwantedBalance: number): { label: string; color: string } {
  if (filled || unwantedBalance < 0.01) return { label: 'RECOVERED', color: 'text-emerald' }
  if (orderId) return { label: 'PENDING', color: 'text-amber' }
  return { label: 'NEEDS SELL', color: 'text-rose' }
}

function formatTxHash(hash: string): string {
  if (!hash) return ''
  return hash.startsWith('0x') ? hash : `0x${hash}`
}

export function PositionExpandedDetails({ position: p, onRefresh }: PositionExpandedDetailsProps) {
  const [loading, setLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const targetStatus = getSideStatus(p.target_clob_filled, p.target_clob_order_id, p.target_unwanted_balance)
  const coverStatus = getSideStatus(p.cover_clob_filled, p.cover_clob_order_id, p.cover_unwanted_balance)

  const handleSell = async (side: 'target' | 'cover', tokenType: 'wanted' | 'unwanted') => {
    setLoading(`${side}-${tokenType}`)
    setError(null)
    try {
      const res = await fetch(`${getApiBaseUrl()}/positions/${p.position_id}/sell`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side, token_type: tokenType }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Sell failed')
      if (!data.success) throw new Error(data.error || 'Order not filled')
      onRefresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setLoading(null)
    }
  }

  const handleMerge = async (side: 'target' | 'cover') => {
    setLoading(`merge-${side}`)
    setError(null)
    try {
      const res = await fetch(`${getApiBaseUrl()}/positions/${p.position_id}/merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Merge failed')
      if (!data.success) throw new Error(data.error || 'Merge failed')
      onRefresh()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed')
    } finally {
      setLoading(null)
    }
  }

  const handleDelete = async () => {
    if (!confirm('Remove from list? Your tokens are NOT affected.')) return
    setDeleting(true)
    try {
      await fetch(`${getApiBaseUrl()}/positions/${p.position_id}`, { method: 'DELETE' })
      onRefresh()
    } catch (e) {
      console.error(e)
    } finally {
      setDeleting(false)
    }
  }

  // Use persisted selling state (survives page refresh)
  const isSellingTarget = p.selling_target || loading === 'target-wanted'
  const isSellingCover = p.selling_cover || loading === 'cover-wanted'

  const targetCanSell = p.target_balance > 0.01 && !p.selling_target
  const targetCanSellUnwanted = p.target_unwanted_balance > 0.01
  const targetCanMerge = Math.min(p.target_balance, p.target_unwanted_balance) > 0.01

  const coverCanSell = p.cover_balance > 0.01 && !p.selling_cover
  const coverCanSellUnwanted = p.cover_unwanted_balance > 0.01
  const coverCanMerge = Math.min(p.cover_balance, p.cover_unwanted_balance) > 0.01

  return (
    <div className="bg-surface-elevated border-t border-border px-4 py-4">
      {error && (
        <div className="mb-3 p-2 bg-rose/10 border border-rose/25 rounded text-rose text-xs">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-6">
        {/* Target */}
        <div>
          <div className="text-[10px] text-text-muted uppercase tracking-wide mb-2">Target</div>
          <p className="text-sm text-text-primary mb-1">{p.target_question}</p>
          <div className="text-xs text-text-muted space-y-0.5 mb-3">
            <p>Position: <span className={p.target_position === 'YES' ? 'text-emerald' : 'text-rose'}>{p.target_position}</span></p>
            <p>Balance: <span className="text-text-secondary">{p.target_balance.toFixed(2)} tokens</span></p>
            <p>Price: ${p.target_entry_price.toFixed(3)} → ${p.target_current_price.toFixed(3)}</p>
            <p>Status: <span className={targetStatus.color}>{targetStatus.label}</span></p>
          </div>

          <div className="flex flex-wrap gap-2">
            {(targetCanSell || isSellingTarget) && (
              <button
                onClick={() => handleSell('target', 'wanted')}
                disabled={loading !== null || isSellingTarget}
                className="px-2 py-1 text-xs bg-rose/15 text-rose hover:bg-rose/25 rounded border border-rose/30 disabled:opacity-50 flex items-center gap-1"
              >
                {isSellingTarget && <span className="w-2.5 h-2.5 border border-current border-t-transparent rounded-full animate-spin" />}
                {isSellingTarget ? 'Selling...' : `Sell ${p.target_position} → ~$${(p.target_balance * p.target_current_price * 0.9).toFixed(2)}`}
              </button>
            )}
            {targetCanSellUnwanted && (
              <button
                onClick={() => handleSell('target', 'unwanted')}
                disabled={loading !== null}
                className="px-2 py-1 text-xs bg-amber/15 text-amber hover:bg-amber/25 rounded border border-amber/30 disabled:opacity-50"
              >
                {loading === 'target-unwanted' ? 'Selling...' : 'Sell unwanted'}
              </button>
            )}
            {targetCanMerge && (
              <button
                onClick={() => handleMerge('target')}
                disabled={loading !== null}
                className="px-2 py-1 text-xs bg-cyan/15 text-cyan hover:bg-cyan/25 rounded border border-cyan/30 disabled:opacity-50"
              >
                {loading === 'merge-target' ? 'Merging...' : `Merge → $${Math.min(p.target_balance, p.target_unwanted_balance).toFixed(2)}`}
              </button>
            )}
          </div>
        </div>

        {/* Cover */}
        <div>
          <div className="text-[10px] text-text-muted uppercase tracking-wide mb-2">Cover</div>
          <p className="text-sm text-text-primary mb-1">{p.cover_question}</p>
          <div className="text-xs text-text-muted space-y-0.5 mb-3">
            <p>Position: <span className={p.cover_position === 'YES' ? 'text-emerald' : 'text-rose'}>{p.cover_position}</span></p>
            <p>Balance: <span className="text-text-secondary">{p.cover_balance.toFixed(2)} tokens</span></p>
            <p>Price: ${p.cover_entry_price.toFixed(3)} → ${p.cover_current_price.toFixed(3)}</p>
            <p>Status: <span className={coverStatus.color}>{coverStatus.label}</span></p>
          </div>

          <div className="flex flex-wrap gap-2">
            {(coverCanSell || isSellingCover) && (
              <button
                onClick={() => handleSell('cover', 'wanted')}
                disabled={loading !== null || isSellingCover}
                className="px-2 py-1 text-xs bg-rose/15 text-rose hover:bg-rose/25 rounded border border-rose/30 disabled:opacity-50 flex items-center gap-1"
              >
                {isSellingCover && <span className="w-2.5 h-2.5 border border-current border-t-transparent rounded-full animate-spin" />}
                {isSellingCover ? 'Selling...' : `Sell ${p.cover_position} → ~$${(p.cover_balance * p.cover_current_price * 0.9).toFixed(2)}`}
              </button>
            )}
            {coverCanSellUnwanted && (
              <button
                onClick={() => handleSell('cover', 'unwanted')}
                disabled={loading !== null}
                className="px-2 py-1 text-xs bg-amber/15 text-amber hover:bg-amber/25 rounded border border-amber/30 disabled:opacity-50"
              >
                {loading === 'cover-unwanted' ? 'Selling...' : 'Sell unwanted'}
              </button>
            )}
            {coverCanMerge && (
              <button
                onClick={() => handleMerge('cover')}
                disabled={loading !== null}
                className="px-2 py-1 text-xs bg-cyan/15 text-cyan hover:bg-cyan/25 rounded border border-cyan/30 disabled:opacity-50"
              >
                {loading === 'merge-cover' ? 'Merging...' : `Merge → $${Math.min(p.cover_balance, p.cover_unwanted_balance).toFixed(2)}`}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="mt-4 pt-3 border-t border-border flex items-center justify-between text-xs text-text-muted">
        <div className="flex items-center gap-3">
          <span>{new Date(p.entry_time).toLocaleString()}</span>
          {p.target_split_tx && (
            <a
              href={`https://polygonscan.com/tx/${formatTxHash(p.target_split_tx)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-cyan hover:underline"
            >
              Target TX ↗
            </a>
          )}
          {p.cover_split_tx && (
            <a
              href={`https://polygonscan.com/tx/${formatTxHash(p.cover_split_tx)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-cyan hover:underline"
            >
              Cover TX ↗
            </a>
          )}
        </div>
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="text-rose hover:underline disabled:opacity-50"
        >
          {deleting ? 'Removing...' : 'Remove'}
        </button>
      </div>
    </div>
  )
}
