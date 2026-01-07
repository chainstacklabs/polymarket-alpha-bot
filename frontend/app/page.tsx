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
    // Refresh every 30 seconds
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [])

  const completedSteps = status?.steps.filter(s => s.has_data).length || 0
  const totalSteps = status?.steps.length || 17

  return (
    <div className="space-y-8 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-4xl font-bold tracking-tight text-text-primary">
            Dashboard
          </h1>
          <p className="text-text-secondary mt-2">
            Real-time alpha opportunities from Polymarket
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div
            className={`
              flex items-center gap-2 px-4 py-2 rounded-lg border
              ${connected
                ? 'bg-emerald/5 border-emerald/20 text-emerald'
                : 'bg-surface border-border text-text-muted'
              }
            `}
          >
            <span className={`relative flex h-2 w-2 ${connected ? '' : 'opacity-50'}`}>
              {connected && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald opacity-75" />
              )}
              <span className={`relative inline-flex rounded-full h-2 w-2 ${connected ? 'bg-emerald' : 'bg-text-muted'}`} />
            </span>
            <span className="text-sm font-medium">
              {connected ? 'Live' : 'Connecting...'}
            </span>
          </div>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="animate-fade-in stagger-1 opacity-0">
          <StatusCard
            title="Pipeline Status"
            value={`${completedSteps}/${totalSteps}`}
            subtitle="steps complete"
            status={completedSteps === totalSteps ? 'success' : 'warning'}
            icon={
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
              </svg>
            }
          />
        </div>
        <div className="animate-fade-in stagger-2 opacity-0">
          <StatusCard
            title="Opportunities"
            value={opportunities.length.toString()}
            subtitle="alpha signals"
            status="info"
            icon={
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 18L9 11.25l4.306 4.307a11.95 11.95 0 015.814-5.519l2.74-1.22m0 0l-5.94-2.28m5.94 2.28l-2.28 5.941" />
              </svg>
            }
          />
        </div>
        <div className="animate-fade-in stagger-3 opacity-0">
          <StatusCard
            title="Live Prices"
            value={Object.keys(prices).length.toString()}
            subtitle="events tracked"
            status={connected ? 'success' : 'warning'}
            icon={
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v12m-3-2.818l.879.659c1.171.879 3.07.879 4.242 0 1.172-.879 1.172-2.303 0-3.182C13.536 12.219 12.768 12 12 12c-.725 0-1.45-.22-2.003-.659-1.106-.879-1.106-2.303 0-3.182s2.9-.879 4.006 0l.415.33M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            }
          />
        </div>
        <div className="animate-fade-in stagger-4 opacity-0">
          <StatusCard
            title="Top Alpha"
            value={opportunities[0]?.alpha.signal_display || '-'}
            subtitle={opportunities[0]?.relation.type || 'No opportunities'}
            status="success"
            icon={
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M11.48 3.499a.562.562 0 011.04 0l2.125 5.111a.563.563 0 00.475.345l5.518.442c.499.04.701.663.321.988l-4.204 3.602a.563.563 0 00-.182.557l1.285 5.385a.562.562 0 01-.84.61l-4.725-2.885a.563.563 0 00-.586 0L6.982 20.54a.562.562 0 01-.84-.61l1.285-5.386a.562.562 0 00-.182-.557l-4.204-3.602a.563.563 0 01.321-.988l5.518-.442a.563.563 0 00.475-.345L11.48 3.5z" />
              </svg>
            }
          />
        </div>
      </div>

      {/* Top Opportunities */}
      <div className="animate-fade-in stagger-5 opacity-0">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="font-display text-2xl font-bold text-text-primary">
              Top Opportunities
            </h2>
            <p className="text-sm text-text-muted mt-1">
              Highest alpha signals from detected market relationships
            </p>
          </div>
          <a
            href="/opportunities"
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-cyan hover:text-cyan/80 transition-colors"
          >
            <span>View all</span>
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
            </svg>
          </a>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="flex items-center gap-3 text-text-muted">
              <svg className="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              <span>Loading opportunities...</span>
            </div>
          </div>
        ) : opportunities.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-4 rounded-xl border border-border bg-surface/50">
            <svg className="w-12 h-12 text-text-muted mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125" />
            </svg>
            <p className="text-text-secondary font-medium mb-2">No opportunities found</p>
            <p className="text-sm text-text-muted text-center max-w-md">
              Run the pipeline to detect alpha opportunities from market relationships.
            </p>
            <a
              href="/pipeline"
              className="mt-4 btn-primary text-sm"
            >
              Go to Pipeline
            </a>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {opportunities.slice(0, 6).map((opp, idx) => (
              <div
                key={opp.id}
                className={`animate-fade-in opacity-0`}
                style={{ animationDelay: `${0.6 + idx * 0.1}s` }}
              >
                <OpportunityCard
                  opportunity={opp}
                  currentPrice={prices[opp.consequence.event_id]?.price}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
