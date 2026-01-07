'use client'

import { useEffect, useState } from 'react'

interface PipelineStep {
  step: string
  name: string
  description: string
  latest_run: string | null
  has_data: boolean
}

interface PipelineStatus {
  timestamp: string
  running: boolean
  current_step: string | null
  steps: PipelineStep[]
}

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
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [])

  async function runPipeline(full: boolean = true) {
    setRunningPipeline(true)
    try {
      const res = await fetch('http://localhost:8000/pipeline/run/production', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ full }),
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

  const formatTimestamp = (ts: string) => {
    try {
      const date = new Date(
        ts.replace(
          /(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/,
          '$1-$2-$3T$4:$5:$6'
        )
      )
      return date.toLocaleString()
    } catch {
      return ts
    }
  }

  const completedSteps = status?.steps.filter(s => s.has_data).length || 0
  const totalSteps = status?.steps.length || 0
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
        <button
          onClick={() => runPipeline(true)}
          disabled={runningPipeline || status?.running}
          className="btn-primary text-xs disabled:opacity-50"
        >
          {runningPipeline || status?.running ? 'Running...' : 'Run Full Pipeline'}
        </button>
      </div>

      {/* Progress Overview */}
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <p className="text-[11px] uppercase tracking-wider text-text-muted mb-1">Progress</p>
            <p className="text-lg font-semibold text-text-primary">
              {completedSteps}/{totalSteps} <span className="text-sm text-text-muted font-normal">steps</span>
            </p>
          </div>
          {status?.running && (
            <div className="flex items-center gap-2 text-xs text-cyan">
              <span className="w-1.5 h-1.5 rounded-full bg-cyan animate-pulse" />
              <span>Running: {status.current_step}</span>
            </div>
          )}
        </div>
        <div className="h-1.5 bg-surface-elevated rounded-full overflow-hidden">
          <div
            className="h-full bg-cyan rounded-full transition-all duration-300"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>

      {/* Steps Table */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <span className="text-sm text-text-muted">Loading...</span>
        </div>
      ) : (
        <div className="rounded-lg border border-border overflow-hidden bg-surface">
          <table className="w-full">
            <thead className="bg-surface-elevated border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-16">Step</th>
                <th className="px-3 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-36">Name</th>
                <th className="px-3 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted">Description</th>
                <th className="px-3 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-40">Last Run</th>
                <th className="px-3 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-text-muted w-20">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {status?.steps.map((step) => {
                const isCurrentStep = status.running && status.current_step === step.step

                return (
                  <tr key={step.step} className={`hover:bg-surface-hover transition-colors ${isCurrentStep ? 'bg-cyan/5' : ''}`}>
                    <td className="px-3 py-2">
                      <span className="text-xs font-mono text-text-muted">{step.step}</span>
                    </td>
                    <td className="px-3 py-2">
                      <span className="text-sm text-text-primary">{step.name}</span>
                    </td>
                    <td className="px-3 py-2">
                      <span className="text-sm text-text-secondary truncate block">{step.description}</span>
                    </td>
                    <td className="px-3 py-2">
                      <span className="text-xs font-mono text-text-muted">
                        {step.latest_run ? formatTimestamp(step.latest_run) : '-'}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      {isCurrentStep ? (
                        <span className="text-xs text-cyan">Running</span>
                      ) : step.has_data ? (
                        <span className="flex items-center gap-1.5">
                          <span className="w-1.5 h-1.5 bg-emerald rounded-full" />
                          <span className="text-xs text-text-muted">Done</span>
                        </span>
                      ) : (
                        <span className="text-xs text-text-muted">—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
