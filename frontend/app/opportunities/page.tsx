'use client'

import { useEffect, useState } from 'react'
import { usePrices } from '@/hooks/usePrices'

interface Opportunity {
  id: string
  rank: number
  trigger: {
    event_id: string
    title: string
    price: number
    price_display: string
    market_url?: string
  }
  consequence: {
    event_id: string
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
}

type SortField = 'rank' | 'alpha' | 'confidence' | 'trigger_price' | 'consequence_price'
type SortDirection = 'asc' | 'desc'

export default function OpportunitiesPage() {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([])
  const [loading, setLoading] = useState(true)
  const [sortField, setSortField] = useState<SortField>('rank')
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')
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

  const SortHeader = ({ field, label, className = '' }: { field: SortField, label: string, className?: string }) => (
    <th
      className={`px-4 py-3.5 text-left text-[10px] font-bold uppercase tracking-[0.15em] text-text-muted cursor-pointer hover:text-cyan transition-colors ${className}`}
      onClick={() => handleSort(field)}
    >
      <div className="flex items-center gap-1.5">
        {label}
        {sortField === field && (
          <svg
            className={`w-3.5 h-3.5 text-cyan transition-transform ${sortDirection === 'desc' ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
          </svg>
        )}
      </div>
    </th>
  )

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-4xl font-bold tracking-tight text-text-primary">
            Opportunities
          </h1>
          <p className="text-text-secondary mt-2">
            {opportunities.length} alpha signals detected
          </p>
        </div>
        <div className="flex items-center gap-4">
          {/* Search */}
          <div className="relative">
            <svg
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
            </svg>
            <input
              type="text"
              placeholder="Filter opportunities..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="pl-10 pr-4 py-2.5 w-64 bg-surface border border-border rounded-lg text-sm text-text-primary placeholder:text-text-muted focus:border-cyan/50 focus:outline-none focus:ring-1 focus:ring-cyan/20 transition-all"
            />
          </div>
          {/* Connection status */}
          <div
            className={`
              flex items-center gap-2 px-4 py-2.5 rounded-lg border
              ${connected
                ? 'bg-emerald/5 border-emerald/20 text-emerald'
                : 'bg-surface border-border text-text-muted'
              }
            `}
          >
            <span className={`relative flex h-2 w-2`}>
              {connected && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald opacity-75" />
              )}
              <span className={`relative inline-flex rounded-full h-2 w-2 ${connected ? 'bg-emerald' : 'bg-text-muted'}`} />
            </span>
            <span className="text-sm font-medium">
              {connected ? 'Live' : 'Offline'}
            </span>
          </div>
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <div className="flex items-center gap-3 text-text-muted">
            <svg className="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span>Loading opportunities...</span>
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-border overflow-hidden bg-surface">
          <div className="overflow-x-auto">
            <table className="w-full terminal-table">
              <thead className="bg-surface-elevated border-b border-border">
                <tr>
                  <SortHeader field="rank" label="#" className="w-16" />
                  <th className="px-4 py-3.5 text-left text-[10px] font-bold uppercase tracking-[0.15em] text-text-muted">
                    Trigger Event
                  </th>
                  <th className="px-4 py-3.5 text-left text-[10px] font-bold uppercase tracking-[0.15em] text-text-muted w-32">
                    Relation
                  </th>
                  <th className="px-4 py-3.5 text-left text-[10px] font-bold uppercase tracking-[0.15em] text-text-muted">
                    Consequence Event
                  </th>
                  <SortHeader field="trigger_price" label="Trigger %" className="w-24" />
                  <SortHeader field="consequence_price" label="Conseq %" className="w-28" />
                  <SortHeader field="alpha" label="Alpha" className="w-28" />
                  <SortHeader field="confidence" label="Conf" className="w-20" />
                  <th className="px-4 py-3.5 text-left text-[10px] font-bold uppercase tracking-[0.15em] text-text-muted w-20">
                    Action
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {sortedOpportunities.map((opp, idx) => {
                  const currentPrice = prices[opp.consequence.event_id]?.price
                  const priceChange = currentPrice !== undefined
                    ? ((currentPrice - opp.consequence.price) / opp.consequence.price) * 100
                    : null
                  const isBuy = opp.alpha.direction === 'BUY'
                  const alphaValue = parseFloat(opp.alpha.signal_display.replace(/[+%]/g, ''))
                  const isHighAlpha = alphaValue > 20

                  return (
                    <tr
                      key={opp.id}
                      className={`
                        transition-colors animate-fade-in opacity-0
                        ${isHighAlpha
                          ? isBuy
                            ? 'bg-alpha-buy/[0.02] hover:bg-alpha-buy/[0.05]'
                            : 'bg-alpha-sell/[0.02] hover:bg-alpha-sell/[0.05]'
                          : 'hover:bg-surface-hover'
                        }
                      `}
                      style={{ animationDelay: `${idx * 0.02}s` }}
                    >
                      <td className="px-4 py-3.5">
                        <span className="text-xs font-mono text-text-muted bg-surface-elevated px-2 py-1 rounded">
                          {opp.rank}
                        </span>
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="max-w-xs">
                          <p className="text-sm text-text-primary truncate" title={opp.trigger.title}>
                            {opp.trigger.title}
                          </p>
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <span className="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider rounded-lg bg-surface-elevated text-text-secondary border border-border">
                          {opp.relation.type}
                        </span>
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="max-w-xs">
                          <p className="text-sm text-text-primary truncate" title={opp.consequence.title}>
                            {opp.consequence.title}
                          </p>
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <span className="text-sm font-mono text-text-secondary">
                          {opp.trigger.price_display}
                        </span>
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-mono text-text-secondary">
                            {opp.consequence.price_display}
                          </span>
                          {priceChange !== null && (
                            <span
                              className={`
                                text-xs font-mono font-semibold px-1.5 py-0.5 rounded
                                ${priceChange > 0
                                  ? 'text-alpha-buy bg-alpha-buy/10'
                                  : priceChange < 0
                                    ? 'text-alpha-sell bg-alpha-sell/10'
                                    : 'text-text-muted'
                                }
                              `}
                            >
                              {priceChange > 0 ? '+' : ''}{priceChange.toFixed(1)}%
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <span
                          className={`
                            inline-flex items-center px-2.5 py-1 text-xs font-bold rounded-lg
                            ${isBuy
                              ? 'bg-alpha-buy/10 text-alpha-buy border border-alpha-buy/20'
                              : 'bg-alpha-sell/10 text-alpha-sell border border-alpha-sell/20'
                            }
                          `}
                        >
                          {opp.alpha.signal_display}
                        </span>
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="flex items-center gap-1.5">
                          <div className="w-10 h-1.5 bg-surface-elevated rounded-full overflow-hidden">
                            <div
                              className="h-full bg-gradient-to-r from-cyan to-cyan-dim rounded-full"
                              style={{ width: `${opp.relation.confidence * 100}%` }}
                            />
                          </div>
                          <span className="text-xs font-mono text-text-muted">
                            {(opp.relation.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <button
                          onClick={() => window.open(`https://polymarket.com/event/${opp.consequence.event_id}`, '_blank')}
                          className="
                            flex items-center gap-1 px-2.5 py-1.5 text-xs font-semibold
                            bg-cyan/10 text-cyan border border-cyan/20 rounded-lg
                            hover:bg-cyan/20 hover:border-cyan/30 transition-all
                          "
                        >
                          <span>View</span>
                          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 19.5l15-15m0 0H8.25m11.25 0v11.25" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Table footer */}
          <div className="px-4 py-3 bg-surface-elevated border-t border-border flex items-center justify-between">
            <p className="text-xs text-text-muted">
              Showing {sortedOpportunities.length} of {opportunities.length} opportunities
            </p>
            <p className="text-xs text-text-muted">
              Last updated: {new Date().toLocaleTimeString()}
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
