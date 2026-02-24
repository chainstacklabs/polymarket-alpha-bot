'use client'

import {
  useState,
  useEffect,
  useCallback,
  useRef,
  useSyncExternalStore,
  memo,
} from 'react'

export interface OrderSettingsValues {
  slippage: number
}

const STORAGE_KEY = 'clob-order-settings'
const SLIPPAGE_PRESETS = [10, 20, 30, 40, 50]

const DEFAULT_SETTINGS: OrderSettingsValues = { slippage: 10 }

export function getOrderSettings(): OrderSettingsValues {
  if (typeof window === 'undefined') return DEFAULT_SETTINGS
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (!stored) return DEFAULT_SETTINGS
    const parsed = JSON.parse(stored)
    return {
      slippage: Math.max(10, Math.min(50, Number(parsed.slippage) || 10)),
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

function saveOrderSettings(settings: OrderSettingsValues) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  // Notify subscribers so useSyncExternalStore picks up the change
  settingsListeners.forEach((l) => l())
}

// External store for useSyncExternalStore
const settingsListeners = new Set<() => void>()
export function subscribeSettings(callback: () => void) {
  settingsListeners.add(callback)
  return () => settingsListeners.delete(callback)
}
export function getSettingsSnapshot() {
  return localStorage.getItem(STORAGE_KEY) || ''
}
export function getSettingsServerSnapshot() {
  return ''
}

interface OrderSettingsProps {
  dropUp?: boolean
}

export const OrderSettings = memo(function OrderSettings({
  dropUp = false,
}: OrderSettingsProps) {
  const [open, setOpen] = useState(false)
  const rawSettings = useSyncExternalStore(
    subscribeSettings,
    getSettingsSnapshot,
    getSettingsServerSnapshot
  )
  const settings: OrderSettingsValues = rawSettings
    ? getOrderSettings()
    : DEFAULT_SETTINGS
  const [customSlippage, setCustomSlippage] = useState('')
  const [slippageError, setSlippageError] = useState('')
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    if (open) document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [open])

  const update = useCallback((patch: Partial<OrderSettingsValues>) => {
    const next = { ...getOrderSettings(), ...patch }
    saveOrderSettings(next)
  }, [])

  const handleCustomSlippage = useCallback(() => {
    const val = parseInt(customSlippage, 10)
    if (isNaN(val) || val < 10 || val > 50) {
      setSlippageError('10-50%')
      return
    }
    setSlippageError('')
    update({ slippage: val })
    setCustomSlippage('')
  }, [customSlippage, update])

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Trigger button — styled like WalletDropdown */}
      <button
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border transition-colors text-xs ${
          open
            ? 'bg-cyan/10 border-cyan/30 text-cyan'
            : 'bg-surface-elevated border-border text-text-muted hover:text-text-secondary hover:border-text-muted/30'
        }`}
        type="button"
      >
        <svg
          className="w-3 h-3"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
          />
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
          />
        </svg>
        <span className="font-mono font-medium text-text-muted/60">Market</span>
        <span className="text-text-muted/40">&middot;</span>
        <span className="font-mono font-medium">{settings.slippage}%</span>
        <svg
          className={`w-3 h-3 transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>

      {/* Dropdown panel — absolute positioned like WalletDropdown */}
      {open && (
        <div
          className={`absolute right-0 w-64 bg-surface border border-border rounded-lg shadow-lg z-50 overflow-hidden ${dropUp ? 'bottom-full mb-1' : 'top-full mt-1'}`}
        >
          {/* Header */}
          <div className="px-3 py-2 border-b border-border bg-surface-elevated">
            <span className="text-xs font-medium text-text-primary">
              Order Settings
            </span>
          </div>

          <div className="p-3 space-y-3">
            {/* Order Type — read-only */}
            <div>
              <span className="text-text-muted text-[10px] uppercase tracking-wide">
                Order Type
              </span>
              <div className="mt-1.5 px-2.5 py-1 rounded text-[11px] font-mono border border-border text-text-secondary bg-surface-elevated/50 w-fit">
                Market (FAK)
              </div>
            </div>

            {/* Slippage */}
            <div>
              <div className="flex items-center gap-1.5">
                <span className="text-text-muted text-[10px] uppercase tracking-wide">
                  Slippage
                </span>
                {settings.slippage > 20 && (
                  <span className="text-amber text-[10px]">High</span>
                )}
              </div>
              <div className="grid grid-cols-3 gap-1 mt-1.5">
                {SLIPPAGE_PRESETS.map((pct) => (
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
                <input
                  type="number"
                  value={customSlippage}
                  onChange={(e) => {
                    setCustomSlippage(e.target.value)
                    setSlippageError('')
                  }}
                  onKeyDown={(e) => e.key === 'Enter' && handleCustomSlippage()}
                  onBlur={handleCustomSlippage}
                  placeholder="Custom"
                  min="10"
                  max="50"
                  className={`px-1.5 py-1 bg-transparent border rounded text-[11px] font-mono text-text-secondary placeholder:text-text-muted/40 focus:outline-none ${slippageError ? 'border-rose/50' : 'border-border focus:border-cyan/50'}`}
                />
              </div>
              {slippageError && (
                <p className="text-[10px] text-rose mt-0.5">{slippageError}</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
})
