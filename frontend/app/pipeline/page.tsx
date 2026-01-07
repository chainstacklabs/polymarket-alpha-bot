'use client'

import { useEffect, useState } from 'react'
import PipelineTimeline from '@/components/PipelineTimeline'
import type { PipelineStatus } from '@/types/pipeline'

export default function PipelinePage() {
  const [status, setStatus] = useState<PipelineStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [runningPipeline, setRunningPipeline] = useState(false)

  async function fetchStatus() {
    try {
      const res = await fetch('http://localhost:8000/pipeline/status')
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

  async function runPipeline(full: boolean = true, maxEvents?: number) {
    setRunningPipeline(true)
    try {
      const res = await fetch('http://localhost:8000/pipeline/run/production', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ full, max_events: maxEvents }),
      })
      if (res.ok) {
        // Start polling for updates
        fetchStatus()
      }
    } catch (error) {
      console.error('Failed to run pipeline:', error)
    } finally {
      setRunningPipeline(false)
    }
  }

  const isRunning = runningPipeline || status?.running
  const stepProgress = status?.step_progress
  const completedSteps = stepProgress?.completed_count || 0
  const totalSteps = stepProgress?.total_steps || 14
  const progressPercent = totalSteps > 0 ? (completedSteps / totalSteps) * 100 : 0

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Pipeline</h1>
          <p className="text-sm text-text-muted mt-0.5">
            Data processing and alpha detection
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => runPipeline(false, 50)}
            disabled={isRunning}
            className="btn-secondary text-xs disabled:opacity-50"
          >
            Run Demo
          </button>
          <button
            onClick={() => runPipeline(false)}
            disabled={isRunning}
            className="btn-secondary text-xs disabled:opacity-50"
          >
            Sync New Events
          </button>
          <button
            onClick={() => runPipeline(true)}
            disabled={isRunning}
            className="btn-primary text-xs disabled:opacity-50"
          >
            {isRunning ? 'Running...' : 'Reprocess All Events'}
          </button>
        </div>
      </div>

      {/* Live Pipeline Progress - shown when running */}
      {isRunning && (
        <PipelineTimeline
          stepProgress={status?.step_progress || null}
          isRunning={isRunning}
        />
      )}

      {/* Progress Overview - shown when not running */}
      {!isRunning && (
        <div className="rounded-lg border border-border bg-surface p-4">
          {loading ? (
            <div className="text-center py-2">
              <span className="text-sm text-text-muted">Loading...</span>
            </div>
          ) : stepProgress ? (
            <>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <p className="text-[11px] uppercase tracking-wider text-text-muted mb-1">
                    Last Run Progress
                  </p>
                  <p className="text-lg font-semibold text-text-primary">
                    {completedSteps}/{totalSteps}{' '}
                    <span className="text-sm text-text-muted font-normal">steps</span>
                  </p>
                </div>
              </div>
              <div className="h-1.5 bg-surface-elevated rounded-full overflow-hidden">
                <div
                  className="h-full bg-emerald rounded-full transition-all duration-300"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
            </>
          ) : (
            <div className="text-center py-2">
              <p className="text-sm text-text-muted">No recent pipeline run</p>
            </div>
          )}
        </div>
      )}

    </div>
  )
}
