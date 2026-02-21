'use client'

import { useState } from 'react'
import type { Position } from '@/app/positions/page'
import { PositionExpandedDetails } from './PositionExpandedDetails'
import { PositionActionsDropdown } from './PositionActionsDropdown'

interface PositionTableRowProps {
  position: Position
  onRefresh: () => void
}

function getStatusIcon(p: Position): {
  icon: string
  color: string
  border: string
} {
  // Red: UNKNOWN status (CLOB failed)
  const hasUnknown =
    (!p.target_clob_filled &&
      !p.target_clob_order_id &&
      p.target_unwanted_balance > 0.01) ||
    (!p.cover_clob_filled &&
      !p.cover_clob_order_id &&
      p.cover_unwanted_balance > 0.01)

  if (hasUnknown) {
    return { icon: '✗', color: 'text-rose', border: 'border-l-rose' }
  }

  // Amber: pending or partial
  if (p.state === 'pending' || p.state === 'partial') {
    return { icon: '⏳', color: 'text-amber', border: 'border-l-amber' }
  }

  // Gray: closed
  if (p.state === 'complete') {
    return { icon: '—', color: 'text-text-muted', border: '' }
  }

  // Green: active and healthy
  return { icon: '✓', color: 'text-emerald', border: '' }
}

function formatRelativeTime(isoString: string): string {
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffMins < 1) return 'now'
  if (diffMins < 60) return `${diffMins}m`
  if (diffHours < 24) return `${diffHours}h`
  if (diffDays < 7) return `${diffDays}d`
  return date.toLocaleDateString()
}

export function PositionTableRow({
  position: p,
  onRefresh,
}: PositionTableRowProps) {
  const [expanded, setExpanded] = useState(false)
  const status = getStatusIcon(p)

  return (
    <>
      <tr
        className={`group cursor-pointer hover:bg-surface-hover transition-colors border-l-2 ${status.border || 'border-l-transparent'}`}
        onClick={() => setExpanded(!expanded)}
      >
        {/* Status */}
        <td className="px-3 py-2.5">
          <span className={`text-base ${status.color}`} title={p.state}>
            {status.icon}
          </span>
        </td>

        {/* Position */}
        <td className="px-3 py-2.5 max-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className={`text-[10px] font-mono px-1.5 py-0.5 rounded shrink-0 ${
                p.target_position === 'YES'
                  ? 'bg-emerald/15 text-emerald'
                  : 'bg-rose/15 text-rose'
              }`}
            >
              {p.target_position}
            </span>
            <span
              className="text-sm text-text-primary truncate"
              title={p.target_question}
            >
              {p.target_question}
            </span>
          </div>
        </td>

        {/* Tokens */}
        <td className="px-3 py-2.5">
          <span className="text-sm font-mono text-text-secondary">
            {p.target_balance.toFixed(1)} / {p.cover_balance.toFixed(1)}
          </span>
        </td>

        {/* Entry */}
        <td className="px-3 py-2.5">
          <span className="text-sm font-mono text-text-secondary">
            ${(p.entry_net_cost ?? p.entry_total_cost).toFixed(0)}
          </span>
        </td>

        {/* Value */}
        <td className="px-3 py-2.5">
          <span className="text-sm font-mono text-text-primary">
            ${p.current_value.toFixed(0)}
          </span>
        </td>

        {/* P&L */}
        <td className="px-3 py-2.5">
          <div>
            <span
              className={`text-sm font-mono font-medium ${p.pnl >= 0 ? 'text-emerald' : 'text-rose'}`}
            >
              {p.pnl >= 0 ? '+' : ''}${p.pnl.toFixed(2)}
            </span>
            <span
              className={`text-[10px] font-mono ml-1 ${p.pnl >= 0 ? 'text-emerald/70' : 'text-rose/70'}`}
            >
              ({p.pnl_pct >= 0 ? '+' : ''}
              {p.pnl_pct.toFixed(0)}%)
            </span>
          </div>
        </td>

        {/* Age */}
        <td className="px-3 py-2.5">
          <span className="text-xs text-text-muted">
            {formatRelativeTime(p.entry_time)}
          </span>
        </td>

        {/* Actions */}
        <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
          <PositionActionsDropdown position={p} onRefresh={onRefresh} />
        </td>
      </tr>

      {/* Expanded row */}
      {expanded && (
        <tr>
          <td colSpan={8} className="p-0">
            <PositionExpandedDetails position={p} onRefresh={onRefresh} />
          </td>
        </tr>
      )}
    </>
  )
}
