'use client'

import { useState, useEffect, useCallback, memo } from 'react'

export interface OrderSettingsValues {
  orderType: 'FAK' | 'FOK' | 'GTC'
  slippage: number
}

const STORAGE_KEY = 'clob-order-settings'
const SLIPPAGE_PRESETS = [10, 20, 30, 40, 50]
const ORDER_TYPES = [
  { value: 'FAK' as const, label: 'FAK', hint: 'Fill available, cancel rest' },
  { value: 'FOK' as const, label: 'FOK', hint: 'All or nothing' },
  { value: 'GTC' as const, label: 'GTC', hint: 'Rest on book until filled' },
]

const DEFAULT_SETTINGS: OrderSettingsValues = { orderType: 'FAK', slippage: 10 }

export function getOrderSettings(): OrderSettingsValues {
  if (typeof window === 'undefined') return DEFAULT_SETTINGS
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (!stored) return DEFAULT_SETTINGS
    const parsed = JSON.parse(stored)
    return {
      orderType: ['FAK', 'FOK', 'GTC'].includes(parsed.orderType) ? parsed.orderType : 'FAK',
      slippage: Math.max(10, Math.min(50, Number(parsed.slippage) || 10)),
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

function saveOrderSettings(settings: OrderSettingsValues) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
}

export const OrderSettings = memo(function OrderSettings() {
  const [expanded, setExpanded] = useState(false)
  const [settings, setSettings] = useState<OrderSettingsValues>(DEFAULT_SETTINGS)
  const [customSlippage, setCustomSlippage] = useState('')

  useEffect(() => {
    setSettings(getOrderSettings())
  }, [])

  const update = useCallback((patch: Partial<OrderSettingsValues>) => {
    setSettings(prev => {
      const next = { ...prev, ...patch }
      saveOrderSettings(next)
      return next
    })
  }, [])

  const handleCustomSlippage = useCallback(() => {
    const val = parseInt(customSlippage, 10)
    if (val >= 10 && val <= 50) {
      update({ slippage: val })
      setCustomSlippage('')
    }
  }, [customSlippage, update])

  return (
    <div className="text-xs">
      <button
        onClick={() => setExpanded(e => !e)}
        className="flex items-center gap-1.5 text-text-muted hover:text-text-secondary transition-colors"
        type="button"
      >
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
        <span className="font-mono">{settings.orderType}</span>
        <span className="text-text-muted/60">·</span>
        <span className="font-mono">{settings.slippage}%</span>
        <svg className={`w-2.5 h-2.5 transition-transform ${expanded ? 'rotate-180' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {expanded && (
        <div className="mt-2 p-2.5 bg-surface-elevated border border-border rounded-lg space-y-2.5">
          {/* Order Type */}
          <div>
            <span className="text-text-muted text-[10px] uppercase tracking-wide">Order Type</span>
            <div className="flex gap-1 mt-1">
              {ORDER_TYPES.map(ot => (
                <button
                  key={ot.value}
                  onClick={() => update({ orderType: ot.value })}
                  className={`px-2 py-1 rounded text-[11px] font-mono border transition-colors ${
                    settings.orderType === ot.value
                      ? 'bg-cyan/15 text-cyan border-cyan/30'
                      : 'bg-transparent text-text-muted border-border hover:border-text-muted/30'
                  }`}
                  title={ot.hint}
                  type="button"
                >
                  {ot.label}
                </button>
              ))}
            </div>
          </div>

          {/* Slippage */}
          <div>
            <div className="flex items-center gap-1.5">
              <span className="text-text-muted text-[10px] uppercase tracking-wide">Slippage</span>
              {settings.slippage > 20 && (
                <span className="text-amber text-[10px]">High</span>
              )}
            </div>
            <div className="flex gap-1 mt-1">
              {SLIPPAGE_PRESETS.map(pct => (
                <button
                  key={pct}
                  onClick={() => update({ slippage: pct })}
                  className={`px-2 py-1 rounded text-[11px] font-mono border transition-colors ${
                    settings.slippage === pct
                      ? 'bg-cyan/15 text-cyan border-cyan/30'
                      : 'bg-transparent text-text-muted border-border hover:border-text-muted/30'
                  }`}
                  type="button"
                >
                  {pct}%
                </button>
              ))}
              <div className="flex">
                <input
                  type="number"
                  value={customSlippage}
                  onChange={e => setCustomSlippage(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleCustomSlippage()}
                  onBlur={handleCustomSlippage}
                  placeholder="Custom"
                  min="10"
                  max="50"
                  className="w-14 px-1.5 py-1 bg-transparent border border-border rounded text-[11px] font-mono text-text-secondary placeholder:text-text-muted/40 focus:outline-none focus:border-cyan/50"
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
})
