'use client'

import { useState, useRef, useEffect } from 'react'
import { useWallet } from '@/hooks/useWallet'

type View = 'status' | 'unlock' | 'generate' | 'import'

export function WalletDropdown() {
  const {
    status,
    loading,
    unlock,
    lock,
    generate,
    importKey,
    approveContracts,
  } = useWallet()
  const [isOpen, setIsOpen] = useState(false)
  const [view, setView] = useState<View>('status')
  const [password, setPassword] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [processing, setProcessing] = useState(false)
  const [copied, setCopied] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false)
        resetForm()
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  function resetForm() {
    setView('status')
    setPassword('')
    setPrivateKey('')
    setError(null)
  }

  async function handleUnlock() {
    if (!password) return
    setProcessing(true)
    setError(null)
    try {
      await unlock(password)
      resetForm()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to unlock')
    } finally {
      setProcessing(false)
    }
  }

  async function handleGenerate() {
    if (!password || password.length < 8) {
      setError('Password must be at least 8 characters')
      return
    }
    setProcessing(true)
    setError(null)
    try {
      await generate(password)
      resetForm()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to generate')
    } finally {
      setProcessing(false)
    }
  }

  async function handleImport() {
    if (!password || password.length < 8) {
      setError('Password must be at least 8 characters')
      return
    }
    if (!privateKey) {
      setError('Private key is required')
      return
    }
    setProcessing(true)
    setError(null)
    try {
      await importKey(privateKey, password)
      resetForm()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to import')
    } finally {
      setProcessing(false)
    }
  }

  async function handleLock() {
    setProcessing(true)
    try {
      await lock()
    } finally {
      setProcessing(false)
    }
  }

  async function handleApprove() {
    setProcessing(true)
    setError(null)
    try {
      await approveContracts()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to approve')
    } finally {
      setProcessing(false)
    }
  }

  function copyAddress() {
    if (status?.address) {
      navigator.clipboard.writeText(status.address)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const truncatedAddress = status?.address
    ? `${status.address.slice(0, 6)}...${status.address.slice(-4)}`
    : null

  // Determine button state
  const isUnlocked = status?.unlocked
  const hasWallet = status?.exists
  const balance = status?.balances?.usdc_e ?? 0

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Trigger Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        disabled={loading}
        className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg border transition-colors ${
          isUnlocked
            ? 'bg-emerald/10 border-emerald/30 text-emerald'
            : hasWallet
              ? 'bg-amber-500/10 border-amber-500/30 text-amber-500'
              : 'bg-surface-elevated border-border text-text-secondary hover:text-text-primary hover:border-text-muted'
        }`}
      >
        {/* Status indicator */}
        <span
          className={`w-2 h-2 rounded-full ${
            loading
              ? 'bg-text-muted animate-pulse'
              : isUnlocked
                ? 'bg-emerald'
                : hasWallet
                  ? 'bg-amber-500'
                  : 'bg-text-muted'
          }`}
        />

        <span className="text-xs font-medium">
          {loading
            ? 'Loading...'
            : isUnlocked
              ? truncatedAddress
              : hasWallet
                ? 'Locked'
                : 'No Wallet'}
        </span>

        {/* Balance when unlocked */}
        {isUnlocked && (
          <span className="text-[10px] font-mono text-emerald/80">
            ${balance.toFixed(2)}
          </span>
        )}

        {/* Dropdown arrow */}
        <svg
          className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-180' : ''}`}
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

      {/* Dropdown Panel */}
      {isOpen && (
        <div className="absolute right-0 top-full mt-1 w-72 bg-surface border border-border rounded-lg shadow-lg z-50 overflow-hidden">
          {/* Header */}
          <div className="px-3 py-2.5 border-b border-border bg-surface-elevated">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-text-primary">
                Wallet
              </span>
              {isUnlocked && (
                <button
                  onClick={handleLock}
                  disabled={processing}
                  className="text-[10px] text-text-muted hover:text-rose transition-colors disabled:opacity-50"
                >
                  Lock
                </button>
              )}
            </div>
          </div>

          {/* Content */}
          <div className="p-3 space-y-3">
            {/* No Wallet State */}
            {!hasWallet && view === 'status' && (
              <div className="space-y-2">
                <p className="text-xs text-text-muted text-center py-2">
                  No wallet configured
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setView('generate')}
                    className="flex-1 px-2.5 py-1.5 rounded text-xs bg-cyan/10 hover:bg-cyan/20 text-cyan border border-cyan/30 transition-colors"
                  >
                    Generate New
                  </button>
                  <button
                    onClick={() => setView('import')}
                    className="flex-1 px-2.5 py-1.5 rounded text-xs bg-surface-elevated hover:bg-surface-hover text-text-secondary transition-colors"
                  >
                    Import Key
                  </button>
                </div>
              </div>
            )}

            {/* Generate Form */}
            {view === 'generate' && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-text-primary">
                    Generate New Wallet
                  </span>
                  <button
                    onClick={resetForm}
                    className="text-[10px] text-text-muted hover:text-text-primary"
                  >
                    Cancel
                  </button>
                </div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleGenerate()}
                  placeholder="Set password (min 8 chars)"
                  autoFocus
                  className="w-full px-2.5 py-1.5 bg-surface-elevated border border-border rounded text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-cyan"
                />
                {error && <p className="text-rose text-[10px]">{error}</p>}
                <button
                  onClick={handleGenerate}
                  disabled={processing || !password}
                  className="w-full px-2.5 py-1.5 rounded text-xs bg-cyan hover:bg-cyan/90 text-void font-medium disabled:opacity-50 transition-colors"
                >
                  {processing ? 'Generating...' : 'Generate'}
                </button>
              </div>
            )}

            {/* Import Form */}
            {view === 'import' && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-text-primary">
                    Import Private Key
                  </span>
                  <button
                    onClick={resetForm}
                    className="text-[10px] text-text-muted hover:text-text-primary"
                  >
                    Cancel
                  </button>
                </div>
                <input
                  type="password"
                  value={privateKey}
                  onChange={(e) => setPrivateKey(e.target.value)}
                  placeholder="Private key (0x...)"
                  autoFocus
                  className="w-full px-2.5 py-1.5 bg-surface-elevated border border-border rounded text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-cyan font-mono"
                />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleImport()}
                  placeholder="Set password (min 8 chars)"
                  className="w-full px-2.5 py-1.5 bg-surface-elevated border border-border rounded text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-cyan"
                />
                {error && <p className="text-rose text-[10px]">{error}</p>}
                <button
                  onClick={handleImport}
                  disabled={processing || !password || !privateKey}
                  className="w-full px-2.5 py-1.5 rounded text-xs bg-cyan hover:bg-cyan/90 text-void font-medium disabled:opacity-50 transition-colors"
                >
                  {processing ? 'Importing...' : 'Import'}
                </button>
              </div>
            )}

            {/* Locked State */}
            {hasWallet && !isUnlocked && view === 'status' && (
              <div className="space-y-2">
                <div className="flex items-center gap-2 py-2">
                  <div className="w-8 h-8 bg-amber-500/20 rounded-full flex items-center justify-center">
                    <svg
                      className="w-4 h-4 text-amber-500"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                      />
                    </svg>
                  </div>
                  <div>
                    <p className="text-xs font-medium text-text-primary">
                      Wallet Locked
                    </p>
                    <p className="text-[10px] text-text-muted font-mono">
                      {truncatedAddress}
                    </p>
                  </div>
                </div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleUnlock()}
                  placeholder="Enter password"
                  autoFocus
                  className="w-full px-2.5 py-1.5 bg-surface-elevated border border-border rounded text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-amber-500"
                />
                {error && <p className="text-rose text-[10px]">{error}</p>}
                <button
                  onClick={handleUnlock}
                  disabled={processing || !password}
                  className="w-full px-2.5 py-1.5 rounded text-xs bg-amber-500 hover:bg-amber-400 text-void font-medium disabled:opacity-50 transition-colors"
                >
                  {processing ? 'Unlocking...' : 'Unlock'}
                </button>
              </div>
            )}

            {/* Unlocked State */}
            {isUnlocked && view === 'status' && (
              <div className="space-y-3">
                {/* Address */}
                <div className="flex items-center justify-between">
                  <span className="text-xs text-text-muted">Address</span>
                  <button
                    onClick={copyAddress}
                    className="flex items-center gap-1 text-xs font-mono text-text-primary hover:text-cyan transition-colors"
                  >
                    {truncatedAddress}
                    <svg
                      className="w-3 h-3"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      {copied ? (
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M5 13l4 4L19 7"
                        />
                      ) : (
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                        />
                      )}
                    </svg>
                  </button>
                </div>

                {/* Balances */}
                <div className="bg-surface-elevated rounded-lg p-2.5 space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-text-muted">USDC.e</span>
                    <span className="font-mono text-text-primary">
                      ${(status?.balances?.usdc_e ?? 0).toFixed(2)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-text-muted">POL (gas)</span>
                    <span className="font-mono text-text-primary">
                      {(status?.balances?.pol ?? 0).toFixed(4)}
                    </span>
                  </div>
                </div>

                {/* Approvals */}
                <div className="flex items-center justify-between text-xs">
                  <span className="text-text-muted">Contract approvals</span>
                  {status?.approvals_set ? (
                    <span className="text-emerald flex items-center gap-1">
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
                          d="M5 13l4 4L19 7"
                        />
                      </svg>
                      Set
                    </span>
                  ) : (
                    <button
                      onClick={handleApprove}
                      disabled={processing}
                      className="text-amber-500 hover:text-amber-400 disabled:opacity-50 transition-colors"
                    >
                      {processing ? 'Approving...' : 'Approve'}
                    </button>
                  )}
                </div>

                {error && <p className="text-rose text-[10px]">{error}</p>}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
