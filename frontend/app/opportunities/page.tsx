'use client'

import { useEffect, useState } from 'react'
import { usePrices } from '@/hooks/usePrices'

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

type SortField = 'rank' | 'alpha' | 'confidence' | 'trigger_price' | 'consequence_price'
type SortDirection = 'asc' | 'desc'

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

export default function OpportunitiesPage() {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([])
  const [loading, setLoading] = useState(true)
  const [sortField, setSortField] = useState<SortField>('rank')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')
  const [selectedOpportunity, setSelectedOpportunity] = useState<Opportunity | null>(null)
  const [filter, setFilter] = useState('')
  const { prices, connected } = usePrices()

  useEffect(() => {
    async function fetchData() {
      try {
        const res = await fetch('http://localhost:8000/data/opportunities?limit=100')
        if (res.ok) {
          const data = await res.json()
          setOpportunities(data.data?.opportunities || [])
        }
      } catch (error) {
        console.error('Failed to fetch opportunities:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDirection('asc')
    }
  }

  const sortedOpportunities = [...opportunities]
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

  return (
    <>
    <div className="space-y-4 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Opportunities</h1>
          <p className="text-sm text-text-muted mt-0.5">
            {opportunities.length} alpha signals
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="Filter..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="px-3 py-1.5 w-40 bg-surface-elevated border border-border rounded text-sm text-text-primary placeholder:text-text-muted focus:border-cyan/50 focus:outline-none transition-colors"
          />
          <div className="flex items-center gap-1.5 text-xs text-text-muted">
            <span className={`w-1.5 h-1.5 rounded-full ${connected ? 'bg-emerald' : 'bg-text-muted'}`} />
            <span>{connected ? 'Live' : 'Offline'}</span>
          </div>
        </div>
      </div>

      {/* Table */}
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
                  <SortHeader field="consequence_price" label="C%" hint="Consequence event market probability (+ live change)" className="w-14" />
                  <SortHeader field="alpha" label="Alpha" hint="Expected profit if trigger occurs. BUY = underpriced, SELL = overpriced" className="w-16" />
                  <SortHeader field="confidence" label="Conf" hint="Model confidence in the relationship" className="w-14" />
                  <th className="px-2.5 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-10"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {sortedOpportunities.map((opp) => {
                  const currentPrice = prices[opp.consequence.event_id]?.price
                  const priceChange = currentPrice !== undefined
                    ? ((currentPrice - opp.consequence.price) / opp.consequence.price) * 100
                    : null
                  const isBuy = opp.alpha.direction === 'BUY'

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
                        <div className="flex items-center gap-1">
                          <span className="text-xs font-mono text-text-muted">
                            {opp.consequence.price_display}
                          </span>
                          {priceChange !== null && (
                            <span className={`text-[10px] font-mono ${priceChange > 0 ? 'text-alpha-buy' : priceChange < 0 ? 'text-alpha-sell' : 'text-text-muted'}`}>
                              {priceChange > 0 ? '+' : ''}{priceChange.toFixed(0)}%
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-2.5 py-2">
                        <span className={`text-xs font-mono font-medium ${isBuy ? 'text-alpha-buy' : 'text-alpha-sell'}`}>
                          {opp.alpha.signal_display}
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
              {sortedOpportunities.length} of {opportunities.length}
            </span>
            <span className="text-[10px] text-text-muted font-mono">
              {new Date().toLocaleTimeString()}
            </span>
          </div>
        </div>
      )}
    </div>

      {/* Opportunity Detail Modal - outside animated container to fix fixed positioning */}
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

            {/* Modal Footer */}
            <div className="flex gap-2 p-4 border-t border-border">
              <a
                href={getMarketUrl(selectedOpportunity.trigger)}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 px-4 py-2 text-center text-sm bg-surface-elevated hover:bg-surface-hover border border-border rounded-lg transition-colors"
              >
                Open Trigger
              </a>
              <a
                href={getMarketUrl(selectedOpportunity.consequence)}
                target="_blank"
                rel="noopener noreferrer"
                className={`flex-1 px-4 py-2 text-center text-sm rounded-lg transition-colors ${
                  selectedOpportunity.alpha.direction === 'BUY'
                    ? 'bg-alpha-buy/20 hover:bg-alpha-buy/30 text-alpha-buy border border-alpha-buy/30'
                    : 'bg-alpha-sell/20 hover:bg-alpha-sell/30 text-alpha-sell border border-alpha-sell/30'
                }`}
              >
                Open Consequence ({selectedOpportunity.alpha.direction})
              </a>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
