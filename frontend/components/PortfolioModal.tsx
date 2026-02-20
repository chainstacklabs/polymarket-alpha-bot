'use client'

import { useState, useEffect, useCallback, memo } from 'react'
import { getCoverageBg } from '@/config/tier-config'
import { useWallet } from '@/hooks/useWallet'
import { getApiBaseUrl } from '@/config/api-config'
import type { Portfolio } from '@/types/portfolio'

type BuyStep = 'idle' | 'unlocking' | 'input' | 'executing' | 'success' | 'error'

interface TradeResult {
  success: boolean
  target: { split_tx?: string; clob_order_id?: string; error?: string }
  cover: { split_tx?: string; clob_order_id?: string; error?: string }
  total_spent: number
  final_balances: { pol: number; usdc_e: number }
  warnings?: string[]
}

interface PortfolioModalProps {
  portfolio: Portfolio
  onClose: () => void
}

// External link icon component - memoized to prevent re-renders
const ExternalLinkIcon = memo(function ExternalLinkIcon({ className = "w-3.5 h-3.5" }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
    </svg>
  )
})

// Info icon for hints - memoized
const InfoIcon = memo(function InfoIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <circle cx="8" cy="8" r="6.5" />
      <path d="M8 7v4" strokeLinecap="round" />
      <circle cx="8" cy="5" r="0.5" fill="currentColor" stroke="none" />
    </svg>
  )
})

// Hint component matching PortfolioTable style - memoized
const HintIcon = memo(function HintIcon({ title, beginner, pro }: { title: string; beginner: string; pro: string }) {
  return (
    <span className="column-hint">
      <span className="column-hint-icon">
        <InfoIcon />
      </span>
      <span className="column-hint-tooltip column-hint-tooltip--top">
        <span className="column-hint-title">{title}</span>
        <span className="column-hint-text">{beginner}</span>
        <span className="column-hint-pro">{pro}</span>
      </span>
    </span>
  )
})

export function PortfolioModal({ portfolio: p, onClose }: PortfolioModalProps) {
  const isProfitable = p.expected_profit > 0.001
  const coverageBg = getCoverageBg(p.coverage)

  // Buy flow state
  const { status, loading: walletLoading, unlock } = useWallet()
  const [buyStep, setBuyStep] = useState<BuyStep>('idle')
  const [amount, setAmount] = useState('10')
  const [password, setPassword] = useState('')
  const [unlocking, setUnlocking] = useState(false)
  const [unlockError, setUnlockError] = useState<string | null>(null)
  const [result, setResult] = useState<TradeResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [executionStep, setExecutionStep] = useState('')

  const apiBase = getApiBaseUrl()
  const MIN_AMOUNT = 5
  const amountNum = parseFloat(amount) || 0
  const totalCost = amountNum * 2
  const hasSufficientBalance = (status?.balances?.usdc_e || 0) >= totalCost
  const meetsMinimum = amountNum >= MIN_AMOUNT
  const needsUnlock = !walletLoading && !status?.unlocked

  // ESC key handler
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      if (buyStep !== 'idle' && buyStep !== 'executing') {
        setBuyStep('idle')
        setError(null)
        setUnlockError(null)
      } else if (buyStep === 'idle') {
        onClose()
      }
    }
  }, [onClose, buyStep])

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = ''
    }
  }, [handleKeyDown])

  const openTargetMarket = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (p.target_group_slug) {
      window.open(`https://polymarket.com/event/${p.target_group_slug}`, '_blank')
    }
  }

  const openCoverMarket = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (p.cover_group_slug) {
      window.open(`https://polymarket.com/event/${p.cover_group_slug}`, '_blank')
    }
  }

  const handleStartBuy = () => {
    if (needsUnlock) {
      setBuyStep('unlocking')
    } else {
      setBuyStep('input')
    }
  }

  const handleUnlock = async () => {
    if (!password) return
    setUnlocking(true)
    setUnlockError(null)
    try {
      await unlock(password)
      setPassword('')
      setBuyStep('input')
    } catch (e) {
      setUnlockError(e instanceof Error ? e.message : 'Failed to unlock')
    } finally {
      setUnlocking(false)
    }
  }

  const handleBuy = async () => {
    if (!hasSufficientBalance || !meetsMinimum) return

    setBuyStep('executing')
    setError(null)
    setExecutionStep('Splitting target position...')

    try {
      const res = await fetch(`${apiBase}/trading/buy-pair`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pair_id: p.pair_id,
          target_market_id: p.target_market_id,
          target_position: p.target_position,
          target_group_slug: p.target_group_slug || '',
          cover_market_id: p.cover_market_id,
          cover_position: p.cover_position,
          cover_group_slug: p.cover_group_slug || '',
          amount_per_position: amountNum,
          skip_clob_sell: false,
        }),
      })

      const data = await res.json()

      if (!res.ok) {
        throw new Error(data.detail || 'Trade failed')
      }

      setResult(data)
      setBuyStep(data.success ? 'success' : 'error')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Trade failed')
      setBuyStep('error')
    }
  }

  const handleCancelBuy = () => {
    setBuyStep('idle')
    setError(null)
    setUnlockError(null)
    setPassword('')
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      {/* Backdrop with blur */}
      <div className="absolute inset-0 bg-void/80 backdrop-blur-sm" />

      {/* Modal */}
      <div
        className="relative w-full max-w-xl bg-surface border border-border rounded-xl shadow-2xl animate-fade-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Top accent line */}
        <div className={`h-0.5 ${coverageBg}`} />

        {/* Close button */}
        <div className="absolute top-3 right-3">
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-surface-elevated transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="px-5 py-5 space-y-4">
          {/* Bet Flow Visualization */}
          <div className="space-y-3">
            {/* Target Bet */}
            <div className="group relative bg-surface-elevated rounded-lg p-3.5 border border-border hover:border-border-glow transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-[10px] font-medium text-text-muted uppercase tracking-wide">Main Bet</span>
                    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${p.target_position === 'YES' ? 'bg-emerald/15 text-emerald' : 'bg-rose/15 text-rose'}`}>
                      {p.target_position} @ ${p.target_price.toFixed(2)}
                    </span>
                    {p.target_group_slug && (
                      <button
                        onClick={openTargetMarket}
                        className="p-0.5 text-text-muted hover:text-cyan transition-colors"
                        title="Open on Polymarket"
                      >
                        <ExternalLinkIcon />
                      </button>
                    )}
                  </div>
                  <p className="text-sm text-text-primary leading-snug">{p.target_question}</p>
                  <p className="text-[11px] text-text-muted mt-1">{p.target_group_title}</p>
                  {p.target_bracket && (
                    <span className="inline-block mt-1 text-[10px] text-text-muted bg-surface px-1.5 py-0.5 rounded border border-border">
                      {p.target_bracket}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Flow Connection */}
            <div className="flex items-center justify-center py-0.5">
              <div className="flex items-center gap-2 text-text-muted">
                <div className="w-6 h-px bg-border" />
                <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface-elevated border border-border">
                  <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                  </svg>
                  <span className="text-[10px] font-medium tracking-wide">Hedged</span>
                </div>
                <div className="w-6 h-px bg-border" />
              </div>
            </div>

            {/* Backup Bet */}
            <div className="group relative bg-surface-elevated rounded-lg p-3.5 border-2 border-cyan/20 hover:border-cyan/40 transition-colors">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span className="text-[10px] font-medium text-cyan uppercase tracking-wide">Backup Bet</span>
                    <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${p.cover_position === 'YES' ? 'bg-emerald/15 text-emerald' : 'bg-rose/15 text-rose'}`}>
                      {p.cover_position} @ ${p.cover_price.toFixed(2)}
                    </span>
                    {p.cover_group_slug && (
                      <button
                        onClick={openCoverMarket}
                        className="p-0.5 text-text-muted hover:text-cyan transition-colors"
                        title="Open on Polymarket"
                      >
                        <ExternalLinkIcon />
                      </button>
                    )}
                  </div>
                  <p className="text-sm text-text-primary leading-snug">{p.cover_question}</p>
                  <p className="text-[11px] text-text-muted mt-1">{p.cover_group_title}</p>
                  {p.cover_bracket && (
                    <span className="inline-block mt-1 text-[10px] text-text-muted bg-surface px-1.5 py-0.5 rounded border border-border">
                      {p.cover_bracket}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* AI Analysis */}
          {p.validation_analysis && (
            <div className="bg-surface-elevated/50 rounded-lg p-3.5 border border-border">
              <h4 className="text-[10px] font-medium text-violet-400 uppercase tracking-wide mb-1">AI Analysis</h4>
              <p className="text-xs text-text-secondary leading-relaxed">{p.validation_analysis}</p>
            </div>
          )}

          {/* Cost + Return */}
          <div className="space-y-1.5">
            <div className="flex gap-3">
              <div className="flex-1 bg-surface-elevated rounded-lg px-3 py-2">
                <p className="text-[10px] text-text-muted uppercase tracking-wide mb-0.5">Total Cost</p>
                <p className="text-base font-mono font-semibold text-text-primary">${p.total_cost.toFixed(2)}</p>
              </div>
              <div className="flex-1 bg-surface-elevated rounded-lg px-3 py-2">
                <p className="text-[10px] text-text-muted uppercase tracking-wide mb-0.5">Potential Return</p>
                <p className={`text-base font-mono font-semibold ${isProfitable ? 'text-emerald' : 'text-rose'}`}>
                  {isProfitable ? '+' : ''}{(p.expected_profit * 100).toFixed(1)}%
                </p>
              </div>
            </div>
            <p className="text-[10px] text-text-muted/70 text-center">
              * Estimates based on current prices, actual results may vary due to liquidity
            </p>
          </div>

          {/* Buy Section - Inline */}
          {buyStep === 'idle' && (
            <button
              onClick={handleStartBuy}
              className="w-full py-2.5 px-4 border border-border hover:border-emerald/50 rounded-lg text-text-primary hover:text-emerald text-sm font-medium transition-all flex items-center justify-center gap-2"
            >
              <span>Buy This Pair</span>
              <HintIcon
                title="How it works"
                beginner="Splits USDC into outcome tokens on-chain (blockchain tx), then sells unwanted sides via CLOB (off-chain, no gas)."
                pro="2 on-chain splitPosition txs → 2 off-chain FOK market orders on Polymarket CLOB."
              />
            </button>
          )}

          {/* Unlock Step */}
          {buyStep === 'unlocking' && (
            <div className="space-y-3 pt-2 border-t border-border">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 bg-amber-500/20 rounded-full flex items-center justify-center">
                  <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-text-primary">Unlock Wallet</p>
                  <p className="text-xs text-text-muted">Enter password to continue</p>
                </div>
              </div>

              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleUnlock()}
                placeholder="Enter password"
                autoFocus
                className="w-full px-3 py-2 bg-surface-elevated border border-border rounded-lg text-text-primary text-sm placeholder:text-text-muted focus:outline-none focus:border-amber-500"
              />

              {unlockError && (
                <p className="text-rose text-xs">{unlockError}</p>
              )}

              <div className="flex gap-2">
                <button
                  onClick={handleCancelBuy}
                  className="flex-1 py-2 px-3 border border-border rounded-lg text-text-muted hover:text-text-primary text-sm transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleUnlock}
                  disabled={unlocking || !password}
                  className="flex-1 py-2 px-3 bg-amber-500 hover:bg-amber-400 rounded-lg text-void text-sm font-medium transition-colors disabled:opacity-50"
                >
                  {unlocking ? 'Unlocking...' : 'Unlock'}
                </button>
              </div>
            </div>
          )}

          {/* Input Step */}
          {buyStep === 'input' && (
            <div className="space-y-3 pt-2 border-t border-border">
              {walletLoading ? (
                <div className="py-4 text-center">
                  <div className="animate-spin w-6 h-6 border-2 border-cyan border-t-transparent rounded-full mx-auto mb-2" />
                  <p className="text-text-muted text-sm">Loading wallet...</p>
                </div>
              ) : (
                <>
                  <div>
                    <label className="text-xs text-text-muted block mb-1">
                      Amount per position <span className="text-text-muted/60">(min $5)</span>
                    </label>
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted text-sm">$</span>
                      <input
                        type="number"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                        min="5"
                        step="1"
                        autoFocus
                        className={`w-full pl-7 pr-3 py-2 bg-surface-elevated border rounded-lg text-text-primary text-sm font-mono focus:outline-none ${!meetsMinimum && amountNum > 0 ? 'border-rose focus:border-rose' : 'border-border focus:border-cyan'}`}
                      />
                    </div>
                    {!meetsMinimum && amountNum > 0 && (
                      <p className="text-rose text-xs mt-1">Minimum $5 required</p>
                    )}
                  </div>

                  <div className="flex justify-between text-sm">
                    <span className="text-text-muted">2 × ${amountNum.toFixed(2)}</span>
                    <span className="text-text-primary font-mono">${totalCost.toFixed(2)} total</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-text-muted">Balance</span>
                    <span className={`font-mono ${hasSufficientBalance ? 'text-emerald' : 'text-rose'}`}>
                      ${(status?.balances?.usdc_e || 0).toFixed(2)} USDC.e
                    </span>
                  </div>

                  {error && (
                    <p className="text-rose text-xs">{error}</p>
                  )}

                  <div className="flex gap-2">
                    <button
                      onClick={handleCancelBuy}
                      className="flex-1 py-2 px-3 border border-border rounded-lg text-text-muted hover:text-text-primary text-sm transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleBuy}
                      disabled={!hasSufficientBalance || !meetsMinimum}
                      className="flex-1 py-2 px-3 bg-cyan hover:bg-cyan/90 rounded-lg text-void text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      Confirm
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {/* Executing Step */}
          {buyStep === 'executing' && (
            <div className="py-4 text-center border-t border-border">
              <div className="animate-spin w-6 h-6 border-2 border-cyan border-t-transparent rounded-full mx-auto mb-2" />
              <p className="text-text-primary text-sm">{executionStep}</p>
              <p className="text-text-muted text-xs mt-1">This may take a minute...</p>
            </div>
          )}

          {/* Success Step */}
          {buyStep === 'success' && result && (
            <div className="space-y-3 pt-2 border-t border-border">
              <div className="flex items-center gap-2">
                <div className={`w-8 h-8 ${result.warnings?.length ? 'bg-amber-500/20' : 'bg-emerald/20'} rounded-full flex items-center justify-center`}>
                  {result.warnings?.length ? (
                    <svg className="w-4 h-4 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                    </svg>
                  ) : (
                    <svg className="w-4 h-4 text-emerald" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </div>
                <div>
                  <p className="text-sm font-medium text-text-primary">
                    {result.warnings?.length ? 'Tokens Acquired — Sell Pending' : 'Purchase Complete'}
                  </p>
                  <p className="text-xs text-text-muted">
                    Spent ${result.total_spent.toFixed(2)} · Balance: ${result.final_balances.usdc_e.toFixed(2)}
                  </p>
                </div>
              </div>

              {result.warnings && result.warnings.length > 0 && (
                <div className="bg-amber-500/10 border border-amber-500/25 rounded-lg p-2 space-y-1">
                  <p className="text-amber-500 text-xs font-medium">
                    Splits succeeded but CLOB sells were blocked.
                  </p>
                  <p className="text-amber-500 text-xs">
                    Enable your proxy, then go to Positions to sell unwanted tokens.
                  </p>
                </div>
              )}

              {(result.target.split_tx || result.cover.split_tx) && (
                <div className="text-xs text-text-muted space-y-0.5">
                  {result.target.split_tx && (
                    <p>Target TX: <code className="text-cyan">{result.target.split_tx.slice(0, 16)}...</code></p>
                  )}
                  {result.cover.split_tx && (
                    <p>Cover TX: <code className="text-cyan">{result.cover.split_tx.slice(0, 16)}...</code></p>
                  )}
                </div>
              )}

              <button
                onClick={onClose}
                className="w-full py-2 px-3 bg-surface-elevated hover:bg-surface border border-border rounded-lg text-text-primary text-sm transition-colors"
              >
                Done
              </button>
            </div>
          )}

          {/* Error Step */}
          {buyStep === 'error' && (
            <div className="space-y-3 pt-2 border-t border-border">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 bg-rose/20 rounded-full flex items-center justify-center">
                  <svg className="w-4 h-4 text-rose" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </div>
                <div>
                  <p className="text-sm font-medium text-text-primary">Trade Failed</p>
                  <p className="text-xs text-rose">{error}</p>
                </div>
              </div>

              <div className="flex gap-2">
                <button
                  onClick={onClose}
                  className="flex-1 py-2 px-3 border border-border rounded-lg text-text-muted text-sm"
                >
                  Close
                </button>
                <button
                  onClick={() => { setBuyStep('input'); setError(null); }}
                  className="flex-1 py-2 px-3 bg-cyan hover:bg-cyan/90 rounded-lg text-void text-sm font-medium"
                >
                  Try Again
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
