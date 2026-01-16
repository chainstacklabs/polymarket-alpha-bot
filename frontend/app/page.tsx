'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { usePrices } from '@/hooks/usePrices'
import { PortfolioModal, type Portfolio } from '@/components/PortfolioModal'
import { TIER_CONFIG } from '@/config/tier-config'

interface PipelineStatus {
  step_progress: {
    completed_count: number
    total_steps: number
  } | null
  production: {
    total_events: number
    last_run: {
      status: string
      completed_at: string | null
    } | null
  } | null
}

interface PortfolioStats {
  total: number
  profitable: number
  byTier: Record<string, number>
  avgCoverage: number
}


// =============================================================================
// HELPERS
// =============================================================================

const formatTime = (isoString: string | null): string => {
  if (!isoString) return '—'
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

// =============================================================================
// MAIN COMPONENT
// =============================================================================

export default function OverviewPage() {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([])
  const [stats, setStats] = useState<PortfolioStats>({ total: 0, profitable: 0, byTier: {}, avgCoverage: 0 })
  const [pipeline, setPipeline] = useState<PipelineStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedPortfolio, setSelectedPortfolio] = useState<Portfolio | null>(null)
  const { connected } = usePrices()

  useEffect(() => {
    async function fetchData() {
      try {
        // Fetch pipeline status first (always available)
        const pipelineRes = await fetch('http://localhost:8000/pipeline/status')
        if (pipelineRes.ok) {
          setPipeline(await pipelineRes.json())
        }

        // Fetch portfolios - may return 404 during pipeline reset
        const [excellentRes, statsRes] = await Promise.all([
          fetch('http://localhost:8000/data/portfolios?limit=4&max_tier=1'),
          fetch('http://localhost:8000/data/portfolios?limit=100&max_tier=3'),
        ])

        if (excellentRes.ok) {
          const data = await excellentRes.json()
          setPortfolios(data.data?.portfolios || [])
        } else if (excellentRes.status === 404) {
          // Data not ready yet (pipeline running after reset)
          setPortfolios([])
        }

        if (statsRes.ok) {
          const data = await statsRes.json()
          const allPortfolios = data.data?.portfolios || []
          // Use meta for true totals (root level values may be filtered)
          setStats({
            total: data.meta?.count || data.total_count || 0,
            profitable: data.meta?.profitable_count || data.profitable_count || 0,
            byTier: data.meta?.by_tier || {},
            avgCoverage: allPortfolios.length > 0
              ? allPortfolios.reduce((acc: number, p: Portfolio) => acc + p.coverage, 0) / allPortfolios.length
              : 0,
          })
        } else if (statsRes.status === 404) {
          // Data not ready yet (pipeline running after reset)
          setStats({ total: 0, profitable: 0, byTier: {}, avgCoverage: 0 })
        }
      } catch (error) {
        // Network error - silently handle, data will refresh on next interval
        console.debug('Fetch interrupted:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [])

  const pipelineComplete = pipeline?.production?.last_run?.status === 'completed'
  const lastRunTime = pipeline?.production?.last_run?.completed_at

  return (
    <>
      <div className="space-y-8 animate-fade-in">
        {/* Header */}
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Overview</h1>
          <p className="text-sm text-text-muted mt-0.5">
            Top hedging pairs with ≥95% win rate — near-guaranteed payouts
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastRunTime && (
            <span className="text-xs text-text-muted">
              Updated {formatTime(lastRunTime)}
            </span>
          )}
          <div className="flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald' : 'bg-text-muted'}`} />
            <span className="text-xs text-text-muted">{connected ? 'Live' : 'Offline'}</span>
          </div>
        </div>
      </header>

      {/* Compact Stats Bar */}
      <section className="bg-surface border border-border rounded-lg p-3">
        <div className="flex items-center justify-between gap-6">
          {/* Key metrics */}
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2">
              <span className="text-2xl font-semibold font-mono text-cyan">{stats.total}</span>
              <div className="text-xs text-text-muted leading-tight">
                <p>strategies</p>
                <p className="text-text-muted/70">{stats.profitable} profitable</p>
              </div>
            </div>
            <div className="w-px h-8 bg-border" />
            <div className="flex items-center gap-2">
              <span className="text-2xl font-semibold font-mono text-amber">
                {stats.avgCoverage > 0 ? `${(stats.avgCoverage * 100).toFixed(0)}%` : '—'}
              </span>
              <span className="text-xs text-text-muted">avg win rate</span>
              {/* Info tooltip */}
              <div className="relative group/winrate">
                <button className="w-4 h-4 rounded-full bg-surface-elevated border border-border text-[10px] text-text-muted hover:text-text-secondary hover:border-text-muted transition-colors flex items-center justify-center">
                  ?
                </button>
                <div className="absolute left-0 top-6 w-56 p-2.5 bg-surface-elevated border border-border rounded-lg shadow-lg opacity-0 invisible group-hover/winrate:opacity-100 group-hover/winrate:visible transition-all z-50">
                  <p className="text-[11px] text-text-secondary">
                    Average probability of getting $1 back across all strategies.
                  </p>
                  <p className="text-[10px] text-text-muted mt-1.5">
                    Higher is better — 100% would mean guaranteed payout on every strategy.
                  </p>
                </div>
              </div>
            </div>
            <div className="w-px h-8 bg-border" />
            <div className="flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${pipelineComplete ? 'bg-emerald' : 'bg-rose'}`} />
              <span className="text-xs text-text-muted">{pipelineComplete ? 'Data ready' : 'Needs refresh'}</span>
            </div>
          </div>

          {/* Tier distribution inline */}
          <div className="flex items-center gap-3">
            {[1, 2, 3].map(tier => {
              const config = TIER_CONFIG[tier]
              const count = stats.byTier[`tier_${tier}`] || 0
              return (
                <div key={tier} className="flex items-center gap-1.5" title={config.desc}>
                  <span className={`text-lg font-semibold font-mono ${config.color}`}>{count}</span>
                  <span className={`text-[10px] ${config.color}`}>{config.label}</span>
                </div>
              )
            })}
            {/* Info tooltip */}
            <div className="relative group ml-1">
              <button className="w-4 h-4 rounded-full bg-surface-elevated border border-border text-[10px] text-text-muted hover:text-text-secondary hover:border-text-muted transition-colors flex items-center justify-center">
                ?
              </button>
              <div className="absolute right-0 top-6 w-80 p-3 bg-surface-elevated border border-border rounded-lg shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all z-50">
                <p className="text-xs font-medium text-text-primary mb-2">How quality tiers work</p>

                <div className="space-y-1 text-[10px] mb-3">
                  <p><span className="text-emerald font-medium">Excellent:</span> ≥95% win rate</p>
                  <p><span className="text-cyan font-medium">Good:</span> 90-95% win rate</p>
                  <p><span className="text-amber font-medium">Fair:</span> 85-90% win rate</p>
                  <p className="text-text-muted/70 italic">Strategies under 85% are filtered out</p>
                </div>

                {/* LLM section */}
                <div className="text-[10px] pt-2 border-t border-border space-y-1.5">
                  <p className="font-medium text-violet-400">LLM reasoning:</p>
                  <p className="text-text-muted"><span className="text-violet-400">•</span> Finds logical implications between markets (A→B)</p>
                  <p className="text-text-muted"><span className="text-violet-400">•</span> Classifies relationship strength: <span className="text-text-secondary">necessary</span>, <span className="text-text-secondary">strong</span>, or <span className="text-text-secondary">inverse</span></p>
                  <p className="text-text-muted"><span className="text-violet-400">•</span> Validates temporal & logical coherence of each pair</p>
                </div>

                {/* Deterministic section */}
                <div className="text-[10px] pt-2 mt-2 border-t border-border space-y-1.5">
                  <p className="font-medium text-cyan">Calculated (deterministic):</p>
                  <p className="text-text-muted"><span className="text-cyan">•</span> Maps strength → probability: necessary=98%, strong=85%, inverse=70%</p>
                  <p className="text-text-muted"><span className="text-cyan">•</span> Derives cover positions via contrapositive logic</p>
                  <p className="text-text-muted"><span className="text-cyan">•</span> Win rate = P(target) + P(¬target) × P(cover)</p>
                  <p className="text-text-muted"><span className="text-cyan">•</span> Tier assigned by win rate thresholds above</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Best Strategies */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-medium text-text-secondary uppercase tracking-wide">
            Best Strategies
          </h2>
          <Link
            href="/portfolios"
            className="text-xs text-text-muted hover:text-cyan transition-colors"
          >
            View all →
          </Link>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12 border border-border rounded-lg bg-surface">
            <span className="text-sm text-text-muted">Loading strategies...</span>
          </div>
        ) : portfolios.length === 0 && stats.total === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 border border-border rounded-lg bg-surface">
            <p className="text-sm text-text-secondary mb-1">No strategies found yet</p>
            <p className="text-xs text-text-muted mb-4">Run the pipeline to discover hedging opportunities</p>
            <Link href="/pipeline" className="btn-primary text-sm">
              Go to Pipeline
            </Link>
          </div>
        ) : portfolios.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 border border-border rounded-lg bg-surface">
            <p className="text-sm text-text-secondary mb-1">{stats.total} strategies found, but none at excellent tier</p>
            <p className="text-xs text-text-muted mb-4">Check lower tiers for available opportunities</p>
            <Link href="/portfolios" className="btn-primary text-sm">
              Explore All Strategies
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {portfolios.map((p, idx) => (
              <PortfolioCard
                key={p.pair_id}
                portfolio={p}
                rank={idx + 1}
                onClick={() => setSelectedPortfolio(p)}
              />
            ))}
          </div>
        )}
      </section>
      </div>

      {/* Portfolio Detail Modal */}
      {selectedPortfolio && (
        <PortfolioModal
          portfolio={selectedPortfolio}
          onClose={() => setSelectedPortfolio(null)}
        />
      )}
    </>
  )
}

// =============================================================================
// PORTFOLIO CARD COMPONENT
// =============================================================================

function PortfolioCard({ portfolio: p, rank, onClick }: { portfolio: Portfolio; rank: number; onClick: () => void }) {
  const config = TIER_CONFIG[p.tier]
  const isProfitable = p.expected_profit > 0.001

  return (
    <button
      onClick={onClick}
      className={`block w-full text-left rounded-lg border ${config.border} bg-surface p-4 hover:bg-surface-hover transition-all group cursor-pointer`}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-text-muted">#{rank}</span>
          <span className={`text-[10px] font-semibold tracking-wider px-2 py-0.5 rounded ${config.bg} ${config.color} border ${config.border}`}>
            {config.label}
          </span>
        </div>
        <div className="text-right">
          <span className={`text-sm font-mono font-semibold ${isProfitable ? 'text-emerald' : 'text-rose'}`}>
            {isProfitable ? '+' : ''}{(p.expected_profit * 100).toFixed(1)}%
          </span>
          <p className="text-[10px] text-text-muted">est. return</p>
        </div>
      </div>

      {/* Target */}
      <div className="mb-2">
        <p className="text-[10px] text-text-muted uppercase tracking-wide mb-0.5">Target Bet</p>
        <p className="text-sm text-text-primary truncate group-hover:text-cyan transition-colors" title={p.target_question}>
          {p.target_question}
        </p>
        <p className="text-[10px] text-text-muted">
          {p.target_position} @ ${p.target_price.toFixed(2)}
        </p>
      </div>

      {/* Cover */}
      <div className="mb-3">
        <p className="text-[10px] text-text-muted uppercase tracking-wide mb-0.5">Backup Bet</p>
        <p className="text-sm text-text-secondary truncate" title={p.cover_question}>
          {p.cover_question}
        </p>
        <p className="text-[10px] text-text-muted">
          {p.cover_position} @ ${p.cover_price.toFixed(2)}
        </p>
      </div>

      {/* Metrics */}
      <div className="flex items-center justify-between pt-3 border-t border-border">
        <div className="flex items-center gap-4">
          <div>
            <span className="text-[10px] text-text-muted">Win Rate</span>
            <p className={`text-sm font-mono ${p.coverage >= 0.95 ? 'text-emerald' : p.coverage >= 0.90 ? 'text-cyan' : 'text-amber'}`}>
              {(p.coverage * 100).toFixed(1)}%
            </p>
          </div>
          <div>
            <span className="text-[10px] text-text-muted">Investment</span>
            <p className="text-sm font-mono text-text-secondary">
              ${p.total_cost.toFixed(2)}
            </p>
          </div>
        </div>

        {/* Mini coverage bar */}
        <div className="w-16">
          <div className="h-1.5 bg-surface-elevated rounded-full overflow-hidden">
            <div
              className={`h-full ${p.coverage >= 0.95 ? 'bg-emerald' : p.coverage >= 0.90 ? 'bg-cyan' : 'bg-amber'}`}
              style={{ width: `${Math.min(100, p.coverage * 100)}%` }}
            />
          </div>
        </div>
      </div>
    </button>
  )
}

