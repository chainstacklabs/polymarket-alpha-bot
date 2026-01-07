'use client'

import { useEffect, useState } from 'react'
import { StatusCard } from '@/components/StatusCard'
import { OpportunityCard } from '@/components/OpportunityCard'
import { usePrices } from '@/hooks/usePrices'

interface PipelineStep {
  step: string
  name: string
  description: string
  latest_run: string | null
  has_data: boolean
}

interface Opportunity {
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

// Generate Polymarket URL
const getMarketUrl = (event: { market_url?: string; slug?: string; event_id: string }) => {
  if (event.market_url) return event.market_url
  const identifier = event.slug || event.event_id
  return `https://polymarket.com/event/${identifier}`
}

export default function Dashboard() {
  const [status, setStatus] = useState<{ steps: PipelineStep[] } | null>(null)
  const [opportunities, setOpportunities] = useState<Opportunity[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedOpportunity, setSelectedOpportunity] = useState<Opportunity | null>(null)
  const { prices, connected } = usePrices()

  useEffect(() => {
    async function fetchData() {
      try {
        const [statusRes, oppsRes] = await Promise.all([
          fetch('http://localhost:8000/pipeline/status'),
          fetch('http://localhost:8000/data/opportunities?limit=10'),
        ])

        if (statusRes.ok) {
          setStatus(await statusRes.json())
        }
        if (oppsRes.ok) {
          const data = await oppsRes.json()
          setOpportunities(data.data?.opportunities || [])
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

  const completedSteps = status?.steps.filter(s => s.has_data).length || 0
  const totalSteps = status?.steps.length || 17

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
        <div className="flex items-center gap-1.5 text-xs text-text-muted">
          <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald' : 'bg-text-muted'}`} />
          <span>{connected ? 'Live' : 'Offline'}</span>
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
          title="Opportunities"
          value={opportunities.length.toString()}
          subtitle="alpha signals"
          status="info"
        />
        <StatusCard
          title="Live Prices"
          value={Object.keys(prices).length.toString()}
          subtitle="events tracked"
          status={connected ? 'success' : 'warning'}
        />
        <StatusCard
          title="Top Alpha"
          value={opportunities[0]?.alpha.signal_display || '-'}
          subtitle={opportunities[0]?.relation.type || 'none'}
          status="success"
        />
      </div>

      {/* Top Opportunities */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-text-primary">Top Opportunities</h2>
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
        ) : opportunities.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 px-4 rounded-lg border border-border bg-surface">
            <p className="text-sm text-text-secondary mb-1">No opportunities found</p>
            <p className="text-xs text-text-muted mb-4">
              Run the pipeline to detect alpha signals
            </p>
            <a href="/pipeline" className="btn-primary text-xs">
              Go to Pipeline
            </a>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {opportunities.slice(0, 6).map((opp) => (
              <OpportunityCard
                key={opp.id}
                opportunity={opp}
                currentPrice={prices[opp.consequence.event_id]?.price}
                onClick={() => setSelectedOpportunity(opp)}
              />
            ))}
          </div>
        )}
      </div>
    </div>

    {/* Opportunity Detail Modal */}
    {selectedOpportunity && (
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
              <span className={`text-sm font-semibold ${selectedOpportunity.alpha.direction === 'BUY' ? 'text-alpha-buy' : 'text-alpha-sell'}`}>
                {selectedOpportunity.alpha.direction} {selectedOpportunity.alpha.signal_display}
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
            <div className={`rounded-lg p-4 border-2 ${selectedOpportunity.alpha.direction === 'BUY' ? 'border-alpha-buy/30 bg-alpha-buy/5' : 'border-alpha-sell/30 bg-alpha-sell/5'}`}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-medium text-text-muted uppercase tracking-wider">THEN (Consequence)</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-text-muted">{selectedOpportunity.consequence.price_display}</span>
                  {prices[selectedOpportunity.consequence.event_id]?.price !== undefined && (
                    <span className={`text-[10px] font-mono ${
                      prices[selectedOpportunity.consequence.event_id].price > selectedOpportunity.consequence.price
                        ? 'text-alpha-buy'
                        : prices[selectedOpportunity.consequence.event_id].price < selectedOpportunity.consequence.price
                        ? 'text-alpha-sell'
                        : 'text-text-muted'
                    }`}>
                      ({((prices[selectedOpportunity.consequence.event_id].price - selectedOpportunity.consequence.price) / selectedOpportunity.consequence.price * 100).toFixed(0)}%)
                    </span>
                  )}
                </div>
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
    )}
    </>
  )
}
