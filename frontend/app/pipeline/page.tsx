'use client'

import { useEffect, useState } from 'react'
import PipelineTimeline from '@/components/PipelineTimeline'
import type { PipelineStatus } from '@/types/pipeline'
import { getApiBaseUrl } from '@/config/api-config'

export default function PipelinePage() {
  const [status, setStatus] = useState<PipelineStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [runningPipeline, setRunningPipeline] = useState(false)

  async function fetchStatus() {
    try {
      const res = await fetch(`${getApiBaseUrl()}/pipeline/status`)
      if (res.ok) {
        setStatus(await res.json())
      }
    } catch (error) {
      console.error('Failed to fetch status:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchStatus()
    // Poll more frequently when running (every 2s), otherwise every 5s
    const interval = setInterval(fetchStatus, status?.running ? 2000 : 5000)
    return () => clearInterval(interval)
  }, [status?.running])

  // Sync local runningPipeline state with server state
  useEffect(() => {
    if (status?.running === false && runningPipeline) {
      setRunningPipeline(false)
    }
  }, [status?.running, runningPipeline])

  async function runPipeline(full: boolean = true, maxEvents?: number) {
    setRunningPipeline(true)
    try {
      const res = await fetch(`${getApiBaseUrl()}/pipeline/run/production`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ full, max_events: maxEvents }),
      })
      if (res.ok) {
        // Start polling for updates - status.running will control the UI state
        fetchStatus()
      } else {
        // Only reset if request failed
        setRunningPipeline(false)
      }
    } catch (error) {
      console.error('Failed to run pipeline:', error)
      setRunningPipeline(false)
    }
    // Don't reset runningPipeline here - let status.running control it
  }

  const isRunning = runningPipeline || status?.running
  const stepProgress = status?.step_progress
  const completedSteps = stepProgress?.completed_count || 0
  const totalSteps = stepProgress?.total_steps || 8
  const progressPercent = totalSteps > 0 ? (completedSteps / totalSteps) * 100 : 0

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Pipeline</h1>
          <p className="text-sm text-text-muted mt-0.5">
            Data processing: {completedSteps} of {totalSteps} steps done
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => runPipeline(false, 50)}
            disabled={isRunning}
            className="btn-secondary text-xs disabled:opacity-50"
            title="Quick test with 50 events"
          >
            Quick Demo
          </button>
          <button
            onClick={() => runPipeline(false)}
            disabled={isRunning}
            className="btn-secondary text-xs disabled:opacity-50"
            title="Add recently created events"
          >
            Add New Events
          </button>
          <button
            onClick={() => runPipeline(true)}
            disabled={isRunning}
            className="btn-primary text-xs disabled:opacity-50"
            title="Rebuild everything from scratch"
          >
            {isRunning ? 'Processing...' : 'Full Rebuild'}
          </button>
        </div>
      </div>

      {/* Pipeline Progress - shown when running OR when there's step data */}
      {(isRunning || stepProgress) ? (
        <PipelineTimeline
          stepProgress={status?.step_progress || null}
          isRunning={isRunning}
        />
      ) : (
        <div className="rounded-lg border border-border bg-surface p-4">
          {loading ? (
            <div className="text-center py-2">
              <span className="text-sm text-text-muted">Loading...</span>
            </div>
          ) : status?.production?.last_run ? (
            <div className="text-center py-4">
              <p className="text-sm text-text-primary mb-2">Previous run finished</p>
              <div className="text-xs text-text-muted space-y-1">
                <p>Result: <span className="text-emerald capitalize">{status.production.last_run.status}</span></p>
                <p>Total events analyzed: {status.production.last_run.events_processed}</p>
                <p>New events found: {status.production.last_run.new_events}</p>
                <p className="text-text-muted/70">
                  {new Date(status.production.last_run.completed_at).toLocaleString()}
                </p>
              </div>
              <p className="text-xs text-text-muted mt-3">
                (Progress details reset on server restart)
              </p>
            </div>
          ) : (
            <div className="text-center py-2">
              <p className="text-sm text-text-muted">No data yet - click a button above to start</p>
            </div>
          )}
        </div>
      )}

    </div>
  )
}
