'use client'

import { useEffect, useState } from 'react'
import { usePrices } from '@/hooks/usePrices'
import { ArbitrageTable } from '@/components/ArbitrageTable'

// =============================================================================
// TYPES
// =============================================================================

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
    confidence_display: string
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

type Tab = 'arbitrage' | 'dependencies'
type SortField = 'rank' | 'alpha' | 'confidence' | 'trigger_price' | 'consequence_price'
type SortDirection = 'asc' | 'desc'

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

// Generate Polymarket URL - prefer market_url, fall back to slug, then event_id
const getMarketUrl = (event: { market_url?: string; slug?: string; event_id: string }) => {
  if (event.market_url) return event.market_url
  const identifier = event.slug || event.event_id
  return `https://polymarket.com/event/${identifier}`
}

// =============================================================================
// MAIN COMPONENT
// =============================================================================

export default function OpportunitiesPage() {
  const [activeTab, setActiveTab] = useState<Tab>('arbitrage')
  const [arbitrageOpportunities, setArbitrageOpportunities] = useState<ArbitrageOpportunity[]>([])
  const [conditionalOpportunities, setConditionalOpportunities] = useState<ConditionalOpportunity[]>([])
  const [loading, setLoading] = useState(true)
  const [sortField, setSortField] = useState<SortField>('rank')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')
  const [selectedOpportunity, setSelectedOpportunity] = useState<ConditionalOpportunity | null>(null)
  const [filter, setFilter] = useState('')
  const { connected } = usePrices()

  // Fetch opportunities based on active tab
  useEffect(() => {
    async function fetchData() {
      setLoading(true)
      try {
        if (activeTab === 'arbitrage') {
          const res = await fetch('http://localhost:8000/data/opportunities?limit=100&type=arbitrage')
          if (res.ok) {
            const data = await res.json()
            setArbitrageOpportunities(data.data?.opportunities || [])
          }
        } else {
          const res = await fetch('http://localhost:8000/data/opportunities?limit=100&type=conditional')
          if (res.ok) {
            const data = await res.json()
            setConditionalOpportunities(data.data?.opportunities || [])
          }
        }
      } catch (error) {
        console.error('Failed to fetch opportunities:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [activeTab])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDirection('asc')
    }
  }

  const sortedConditionalOpportunities = [...conditionalOpportunities]
    .filter(opp => {
      if (!filter) return true
      const search = filter.toLowerCase()
      return (
        opp.trigger.title.toLowerCase().includes(search) ||
        opp.consequence.title.toLowerCase().includes(search) ||
        opp.relation.type.toLowerCase().includes(search)
      )
    })
    .sort((a, b) => {
      let aVal: number, bVal: number
      switch (sortField) {
        case 'rank':
          aVal = a.rank
          bVal = b.rank
          break
        case 'alpha':
          aVal = a.alpha.signal
          bVal = b.alpha.signal
          break
        case 'confidence':
          aVal = a.relation.confidence
          bVal = b.relation.confidence
          break
        case 'trigger_price':
          aVal = a.trigger.price
          bVal = b.trigger.price
          break
        case 'consequence_price':
          aVal = a.consequence.price
          bVal = b.consequence.price
          break
        default:
          aVal = a.rank
          bVal = b.rank
      }
      return sortDirection === 'asc' ? aVal - bVal : bVal - aVal
    })

  const SortHeader = ({ field, label, hint, className = '' }: { field: SortField, label: string, hint: string, className?: string }) => (
    <th
      className={`px-2.5 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted cursor-pointer hover:text-text-secondary transition-colors ${className}`}
      onClick={() => handleSort(field)}
      title={hint}
    >
      <div className="flex items-center gap-1">
        {label}
        {sortField === field && (
          <span className={`text-cyan ${sortDirection === 'desc' ? 'rotate-180' : ''}`}>↑</span>
        )}
      </div>
    </th>
  )

  const opportunityCount = activeTab === 'arbitrage'
    ? arbitrageOpportunities.length
    : conditionalOpportunities.length

  return (
    <>
    <div className="space-y-4 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Opportunities</h1>
          <p className="text-sm text-text-muted mt-0.5">
            {opportunityCount} {activeTab === 'arbitrage' ? 'arbitrage' : 'conditional'} signals
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 text-xs text-text-muted">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald' : 'bg-text-muted'}`} />
            <span>{connected ? 'Live' : 'Offline'}</span>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 p-1 bg-surface-elevated rounded-lg border border-border w-fit">
        <button
          onClick={() => setActiveTab('arbitrage')}
          className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${
            activeTab === 'arbitrage'
              ? 'bg-surface text-text-primary shadow-sm'
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Arbitrage
          <span className="ml-1.5 text-xs text-emerald font-mono">
            {arbitrageOpportunities.length || '-'}
          </span>
        </button>
        <button
          onClick={() => setActiveTab('dependencies')}
          className={`px-4 py-1.5 rounded text-sm font-medium transition-colors ${
            activeTab === 'dependencies'
              ? 'bg-surface text-text-primary shadow-sm'
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Dependencies
          <span className="ml-1.5 text-xs text-text-muted font-mono">
            {conditionalOpportunities.length || '-'}
          </span>
        </button>
      </div>

      {/* Content */}
      {activeTab === 'arbitrage' ? (
        <ArbitrageTable
          opportunities={arbitrageOpportunities}
          loading={loading}
        />
      ) : (
        <>
          {/* Filter for Dependencies */}
          <div className="flex items-center justify-end">
            <input
              type="text"
              placeholder="Filter..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="px-3 py-1.5 w-40 bg-surface-elevated border border-border rounded text-sm text-text-primary placeholder:text-text-muted focus:border-cyan/50 focus:outline-none transition-colors"
            />
          </div>

          {/* Dependencies Table */}
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <span className="text-sm text-text-muted">Loading...</span>
            </div>
          ) : (
            <div className="rounded-lg border border-border overflow-hidden bg-surface">
              <div className="overflow-x-auto">
                <table className="w-full table-fixed">
                  <thead className="bg-surface-elevated border-b border-border">
                    <tr>
                      <SortHeader field="rank" label="#" hint="Opportunity rank by alpha strength" className="w-10" />
                      <th className="px-2.5 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[24%]" title="IF this event happens...">
                        Trigger
                      </th>
                      <th className="px-2.5 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-28" title="Relationship type between trigger and consequence events">
                        Type
                      </th>
                      <th className="px-2.5 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-[24%]" title="...THEN this event is affected">
                        Consequence
                      </th>
                      <SortHeader field="trigger_price" label="T%" hint="Trigger event market probability" className="w-12" />
                      <SortHeader field="consequence_price" label="C%" hint="Consequence event market probability (live)" className="w-14" />
                      <SortHeader field="alpha" label="Alpha" hint="Expected profit if trigger occurs. BUY YES = underpriced, BUY NO = overpriced" className="w-16" />
                      <SortHeader field="confidence" label="Conf" hint="Model confidence in the relationship" className="w-14" />
                      <th className="px-2.5 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-10"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {sortedConditionalOpportunities.map((opp) => {
                      // Backend already recalculates alpha with live prices
                      const isBuy = opp.alpha.signal > 0
                      // Always show positive alpha magnitude
                      const alphaDisplay = `${isBuy ? 'YES' : 'NO'} +${Math.abs(opp.alpha.signal * 100).toFixed(0)}%`

                      return (
                        <tr
                          key={opp.id}
                          className="hover:bg-surface-hover transition-colors cursor-pointer"
                          onClick={() => setSelectedOpportunity(opp)}
                        >
                          <td className="px-2.5 py-2">
                            <span className="text-xs font-mono text-text-muted">{opp.rank}</span>
                          </td>
                          <td className="px-2.5 py-2">
                            <p className="text-sm text-text-primary truncate" title={opp.trigger.title}>
                              {opp.trigger.title}
                            </p>
                          </td>
                          <td className="px-2.5 py-2">
                            <span
                              className="text-[10px] uppercase text-text-muted truncate block cursor-help"
                              title={getRelationHint(opp.relation.type)}
                            >
                              {opp.relation.type}
                            </span>
                          </td>
                          <td className="px-2.5 py-2">
                            <p className="text-sm text-text-primary truncate" title={opp.consequence.title}>
                              {opp.consequence.title}
                            </p>
                          </td>
                          <td className="px-2.5 py-2">
                            <span className="text-xs font-mono text-text-muted">
                              {opp.trigger.price_display}
                            </span>
                          </td>
                          <td className="px-2.5 py-2">
                            <span className="text-xs font-mono text-text-muted">
                              {opp.consequence.price_display}
                            </span>
                          </td>
                          <td className="px-2.5 py-2">
                            <span
                              className={`text-xs font-mono font-medium cursor-help ${isBuy ? 'text-alpha-buy' : 'text-alpha-sell'}`}
                              title="Potential profit margin if the trigger event occurs"
                            >
                              {alphaDisplay}
                            </span>
                          </td>
                          <td className="px-2.5 py-2">
                            <span className="text-xs font-mono text-text-muted">
                              {(opp.relation.confidence * 100).toFixed(0)}%
                            </span>
                          </td>
                          <td className="px-2.5 py-2">
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                setSelectedOpportunity(opp)
                              }}
                              className="text-xs text-text-muted hover:text-cyan transition-colors"
                              title="View opportunity details"
                            >
                              ↗
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              {/* Footer */}
              <div className="px-2.5 py-2 bg-surface-elevated border-t border-border flex items-center justify-between">
                <span className="text-[10px] text-text-muted">
                  {sortedConditionalOpportunities.length} of {conditionalOpportunities.length}
                </span>
                <span className="text-[10px] text-text-muted font-mono">
                  {new Date().toLocaleTimeString()}
                </span>
              </div>
            </div>
          )}
        </>
      )}
    </div>

      {/* Conditional Opportunity Detail Modal - outside animated container to fix fixed positioning */}
      {selectedOpportunity && (() => {
        // Backend already recalculates alpha with live prices
        const isBuy = selectedOpportunity.alpha.signal > 0
        // Always show positive alpha magnitude - direction is indicated by BUY YES/NO
        const alphaDisplay = `+${Math.abs(selectedOpportunity.alpha.signal * 100).toFixed(0)}%`
        // Calculate correct price based on action (YES price vs NO price) - show as dollar amount
        const actionPrice = isBuy
          ? `$${selectedOpportunity.consequence.price.toFixed(2)}`
          : `$${(1 - selectedOpportunity.consequence.price).toFixed(2)}`

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
                <span
                  className={`text-sm font-semibold cursor-help ${isBuy ? 'text-alpha-buy' : 'text-alpha-sell'}`}
                  title="Potential profit margin if the trigger event occurs"
                >
                  {isBuy ? 'BUY YES' : 'BUY NO'} {alphaDisplay}
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
                    `If "${selectedOpportunity.trigger.title.slice(0, 60)}..." resolves to YES, ${isBuy ? 'buy YES shares of' : 'buy NO shares of'} "${selectedOpportunity.consequence.title.slice(0, 60)}..." at the current price of ${actionPrice}.`}
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
