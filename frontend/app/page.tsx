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
    title: string
    price: number
    price_display: string
  }
  consequence: {
    event_id: string
    title: string
    price: number
    price_display: string
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
}

export default function Dashboard() {
  const [status, setStatus] = useState<{ steps: PipelineStep[] } | null>(null)
  const [opportunities, setOpportunities] = useState<Opportunity[]>([])
  const [loading, setLoading] = useState(true)
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
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
