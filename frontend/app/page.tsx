'use client'

import { useEffect, useState } from 'react'
import { StatusCard } from '@/components/StatusCard'
import { OpportunityCard } from '@/components/OpportunityCard'
import { usePrices } from '@/hooks/usePrices'

// =============================================================================
// TYPES
// =============================================================================

interface StepProgressData {
  completed_count: number
  total_steps: number
}

interface LastRun {
  id: number
  run_type: string
  started_at: string
  completed_at: string | null
  events_processed: number
  new_events: number
  status: string
}

interface ProductionState {
  total_events: number
  total_entities: number
  total_edges: number
  last_full_run: string | null
  last_refresh: string | null
  last_run: LastRun | null
}

interface PipelineStatus {
  step_progress: StepProgressData | null
  production: ProductionState | null
}

interface ConditionalOpportunity {
  id: string
  rank: number
  trigger: {
    event_id: string
    slug?: string
    title: string
    price: number
    price_display: string
    market_url?: string
  }
  consequence: {
    event_id: string
    slug?: string
    title: string
    price: number
    price_display: string
    market_url?: string
  }
  relation: {
    type: string
    type_display: string
    confidence: number
  }
  alpha: {
    signal: number
    signal_display: string
    direction: string
  }
  strategy?: {
    summary?: string
    detailed?: string
    action?: string
  }
}

interface ArbitragePosition {
  event_id: string
  title: string
  slug: string | null
  position: 'YES' | 'NO'
  price: number
  price_display: string
  outcome_covered: string
  market_url: string
}

interface ArbitrageOpportunity {
  signal_id: string
  opportunity_type: 'arbitrage'
  positions: ArbitragePosition[]
  total_cost: number
  total_cost_display: string
  profit: number
  profit_display: string
  num_markets: number
  confidence: number
  confidence_adjusted_profit: number
  reasoning: string
  strategy: {
    description: string
  }
}

// =============================================================================
// CONSTANTS
// =============================================================================

// Relation type explanations for tooltips
const RELATION_HINTS: Record<string, string> = {
  'DIRECT_CAUSE': 'A directly causes B to happen (high probability)',
  'ENABLING_CONDITION': 'A makes B more likely, but doesn\'t guarantee it',
  'INHIBITING_CONDITION': 'A reduces the likelihood of B happening',
  'REQUIRES': 'B cannot happen unless A happens first',
  'CORRELATED': 'A and B tend to move together, but unclear which causes which',
  'TIMEFRAME_VARIANT': 'Same event with different time deadlines',
  'THRESHOLD_VARIANT': 'Same event with different numeric thresholds',
  'MUTUALLY_EXCLUSIVE': 'If A happens, B cannot happen (and vice versa)',
}

const getRelationHint = (type: string): string => {
  const normalized = type.toUpperCase().replace(/\s+/g, '_')
  return RELATION_HINTS[normalized] || type
}

// Format timestamp to human-readable relative time
const formatRelativeTime = (isoString: string): string => {
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`

  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

// Format timestamp to full date/time
const formatDateTime = (isoString: string): string => {
  const date = new Date(isoString)
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
}

// Generate Polymarket URL
const getMarketUrl = (event: { market_url?: string; slug?: string; event_id: string }) => {
  if (event.market_url) return event.market_url
  const identifier = event.slug || event.event_id
  return `https://polymarket.com/event/${identifier}`
}

// =============================================================================
// MAIN COMPONENT
// =============================================================================

export default function Dashboard() {
  const [status, setStatus] = useState<PipelineStatus | null>(null)
  const [arbitrageOpportunities, setArbitrageOpportunities] = useState<ArbitrageOpportunity[]>([])
  const [conditionalOpportunities, setConditionalOpportunities] = useState<ConditionalOpportunity[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedOpportunity, setSelectedOpportunity] = useState<ConditionalOpportunity | null>(null)
  const { prices, connected } = usePrices()

  useEffect(() => {
    async function fetchData() {
      try {
        const [statusRes, arbRes, condRes] = await Promise.all([
          fetch('http://localhost:8000/pipeline/status'),
          fetch('http://localhost:8000/data/opportunities?type=arbitrage&limit=10'),
          fetch('http://localhost:8000/data/opportunities?type=conditional&limit=10'),
        ])

        if (statusRes.ok) {
          setStatus(await statusRes.json())
        }
        if (arbRes.ok) {
          const data = await arbRes.json()
          setArbitrageOpportunities(data.data?.opportunities || [])
        }
        if (condRes.ok) {
          const data = await condRes.json()
          setConditionalOpportunities(data.data?.opportunities || [])
        }
      } catch (error) {
        console.error('Failed to fetch data:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [])

  // Use step_progress if available, otherwise check if last run completed
  const lastRunCompleted = status?.production?.last_run?.status === 'completed'
  const completedSteps = status?.step_progress?.completed_count ?? (lastRunCompleted ? 14 : 0)
  const totalSteps = status?.step_progress?.total_steps || 14

  return (
    <>
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Dashboard</h1>
          <p className="text-sm text-text-muted mt-0.5">
            Alpha opportunities from Polymarket
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="flex items-center gap-1.5 text-xs text-text-muted">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald' : 'bg-text-muted'}`} />
            <span>{connected ? 'Live' : 'Offline'}</span>
          </div>
          {status?.production?.last_run?.completed_at && (
            <div
              className="text-[10px] text-text-muted cursor-help"
              title={`Last snapshot: ${formatDateTime(status.production.last_run.completed_at)}\nEvents processed: ${status.production.last_run.events_processed || 0}`}
            >
              Events snapshot: {formatRelativeTime(status.production.last_run.completed_at)}
            </div>
          )}
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatusCard
          title="Pipeline"
          value={`${completedSteps}/${totalSteps}`}
          subtitle="steps complete"
          status={completedSteps === totalSteps ? 'success' : 'warning'}
        />
        <StatusCard
          title="Arbitrage"
          value={arbitrageOpportunities.length.toString()}
          subtitle="cross-market"
          status={arbitrageOpportunities.length > 0 ? 'success' : 'info'}
        />
        <StatusCard
          title="Conditional"
          value={conditionalOpportunities.length.toString()}
          subtitle="dependencies"
          status="info"
        />
        <StatusCard
          title="Live Prices"
          value={Object.keys(prices).length.toString()}
          subtitle="events tracked"
          status={connected ? 'success' : 'warning'}
        />
      </div>

      {/* Arbitrage Section - PRIMARY */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-emerald" />
            <h2 className="text-sm font-medium text-text-primary">Cross-Market Arbitrage</h2>
            <span className="text-[10px] text-text-muted px-1.5 py-0.5 bg-surface-elevated rounded border border-border">
              PRIMARY
            </span>
          </div>
          <a
            href="/opportunities"
            className="text-xs text-text-secondary hover:text-cyan transition-colors"
          >
            View all →
          </a>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <span className="text-sm text-text-muted">Loading...</span>
          </div>
        ) : arbitrageOpportunities.length === 0 ? (
          <div className="rounded-lg border border-emerald/20 bg-emerald/5 p-6">
            <div className="flex flex-col items-center justify-center text-center">
              <div className="w-10 h-10 rounded-full bg-emerald/10 flex items-center justify-center mb-3">
                <span className="text-emerald text-lg">⬡</span>
              </div>
              <p className="text-sm text-text-secondary mb-1">No arbitrage opportunities detected</p>
              <p className="text-xs text-text-muted max-w-md">
                Monitoring for mispriced exhaustive sets across different markets.
                Arbitrage appears when outcomes from multiple events can be fully hedged at a combined cost below 100%.
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {arbitrageOpportunities.slice(0, 4).map((opp) => (
              <ArbitrageCard key={opp.signal_id} opportunity={opp} />
            ))}
          </div>
        )}
      </div>

      {/* Conditional Section - SECONDARY */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-text-muted" />
            <h2 className="text-sm font-medium text-text-secondary">Event Dependencies</h2>
          </div>
          <a
            href="/opportunities"
            className="text-xs text-text-secondary hover:text-cyan transition-colors"
          >
            View all →
          </a>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <span className="text-sm text-text-muted">Loading...</span>
          </div>
        ) : conditionalOpportunities.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 px-4 rounded-lg border border-border bg-surface">
            <p className="text-sm text-text-secondary mb-1">No conditional opportunities found</p>
            <p className="text-xs text-text-muted mb-4">
              Run the pipeline to detect event dependencies
            </p>
            <a href="/pipeline" className="btn-primary text-xs">
              Go to Pipeline
            </a>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {conditionalOpportunities.slice(0, 4).map((opp) => (
              <OpportunityCard
                key={opp.id}
                opportunity={opp}
                onClick={() => setSelectedOpportunity(opp)}
              />
            ))}
          </div>
        )}
      </div>
    </div>

    {/* Conditional Opportunity Detail Modal */}
    {selectedOpportunity && (() => {
      // Backend already recalculates alpha with live prices
      const isBuy = selectedOpportunity.alpha.signal > 0

      return (
      <div
        className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
        onClick={() => setSelectedOpportunity(null)}
      >
        <div
          className="bg-surface border border-border rounded-lg max-w-2xl w-full max-h-[90vh] overflow-y-auto animate-fade-in"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Modal Header */}
          <div className="flex items-center justify-between p-4 border-b border-border">
            <div className="flex items-center gap-3">
              <span className="text-sm font-mono text-text-muted">#{selectedOpportunity.rank}</span>
              <span className={`text-sm font-semibold ${isBuy ? 'text-alpha-buy' : 'text-alpha-sell'}`}>
                {isBuy ? 'BUY YES' : 'BUY NO'} {selectedOpportunity.alpha.signal_display}
              </span>
            </div>
            <button
              onClick={() => setSelectedOpportunity(null)}
              className="text-text-muted hover:text-text-primary transition-colors text-xl"
            >
              ×
            </button>
          </div>

          {/* Modal Content */}
          <div className="p-4 space-y-4">
            {/* Trigger Event */}
            <div className="bg-surface-elevated rounded-lg p-4 border border-border">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-medium text-text-muted uppercase tracking-wider">IF (Trigger)</span>
                <span className="text-xs font-mono text-text-muted">{selectedOpportunity.trigger.price_display}</span>
              </div>
              <p className="text-sm text-text-primary mb-3">{selectedOpportunity.trigger.title}</p>
              <a
                href={getMarketUrl(selectedOpportunity.trigger)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-xs text-cyan hover:text-cyan/80 transition-colors"
              >
                View on Polymarket →
              </a>
            </div>

            {/* Relation Type */}
            <div className="flex items-center justify-center py-2">
              <div className="flex items-center gap-2 text-text-muted">
                <div className="h-px w-8 bg-border" />
                <span
                  className="text-[10px] uppercase tracking-wide px-2 py-1 rounded bg-surface-elevated border border-border cursor-help"
                  title={getRelationHint(selectedOpportunity.relation.type)}
                >
                  {selectedOpportunity.relation.type}
                </span>
                <span className="text-[10px] font-mono">
                  ({(selectedOpportunity.relation.confidence * 100).toFixed(0)}% conf)
                </span>
                <div className="h-px w-8 bg-border" />
              </div>
            </div>

            {/* Consequence Event */}
            <div className={`rounded-lg p-4 border-2 ${isBuy ? 'border-alpha-buy/30 bg-alpha-buy/5' : 'border-alpha-sell/30 bg-alpha-sell/5'}`}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-medium text-text-muted uppercase tracking-wider">THEN (Consequence)</span>
                <span className="text-xs font-mono text-text-muted">
                  {selectedOpportunity.consequence.price_display}
                </span>
              </div>
              <p className="text-sm text-text-primary mb-3">{selectedOpportunity.consequence.title}</p>
              <a
                href={getMarketUrl(selectedOpportunity.consequence)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-xs text-cyan hover:text-cyan/80 transition-colors"
              >
                View on Polymarket →
              </a>
            </div>

            {/* Strategy */}
            <div className="bg-surface-elevated rounded-lg p-4 border border-border">
              <h4 className="text-[10px] font-medium text-text-muted uppercase tracking-wider mb-2">Strategy</h4>
              <p className="text-sm text-text-secondary">
                {selectedOpportunity.strategy?.detailed ||
                  `If "${selectedOpportunity.trigger.title.slice(0, 60)}..." resolves to YES, ${selectedOpportunity.alpha.direction === 'BUY' ? 'buy' : 'sell'} "${selectedOpportunity.consequence.title.slice(0, 60)}..." at the current price of ${selectedOpportunity.consequence.price_display}.`}
              </p>
            </div>

            {/* Relation Type Explanation */}
            <div className="text-xs text-text-muted bg-surface-elevated rounded p-3 border border-border">
              <span className="font-medium">{selectedOpportunity.relation.type}:</span>{' '}
              {getRelationHint(selectedOpportunity.relation.type)}
            </div>
          </div>
        </div>
      </div>
      )
    })()}
    </>
  )
}

// =============================================================================
// ARBITRAGE CARD COMPONENT
// =============================================================================

function ArbitrageCard({ opportunity }: { opportunity: ArbitrageOpportunity }) {
  const profitPercent = Math.round(opportunity.profit * 100)
  const totalCostPercent = Math.round(opportunity.total_cost * 100)

  return (
    <a
      href="/opportunities"
      className="bg-surface border border-emerald/20 rounded-lg p-4 border-l-2 border-l-emerald transition-colors hover:bg-surface-hover block"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-text-muted">
            {opportunity.signal_id}
          </span>
          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted border border-border">
            {opportunity.num_markets} markets
          </span>
        </div>
        <span className="text-sm font-semibold font-mono text-emerald">
          +{profitPercent}% profit
        </span>
      </div>

      {/* Positions Summary */}
      <div className="space-y-1.5 mb-3">
        {opportunity.positions.slice(0, 2).map((position, idx) => (
          <div key={idx} className="flex items-center gap-2">
            <span
              className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                position.position === 'YES'
                  ? 'bg-emerald/10 text-emerald'
                  : 'bg-amber/10 text-amber'
              }`}
            >
              {position.position}
            </span>
            <span className="text-sm text-text-primary truncate flex-1" title={position.title}>
              {position.title}
            </span>
            <span className="text-xs font-mono text-text-muted">
              {position.price_display}
            </span>
          </div>
        ))}
        {opportunity.positions.length > 2 && (
          <span className="text-xs text-text-muted">
            +{opportunity.positions.length - 2} more
          </span>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between pt-3 border-t border-border">
        <div className="text-xs text-text-muted">
          Sum: <span className="font-mono">{totalCostPercent}%</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-10 h-1 bg-surface-elevated rounded-full overflow-hidden">
            <div
              className="h-full bg-cyan rounded-full"
              style={{ width: `${opportunity.confidence * 100}%` }}
            />
          </div>
          <span className="text-[10px] font-mono text-text-muted">
            {Math.round(opportunity.confidence * 100)}%
          </span>
        </div>
      </div>
    </a>
  )
}
