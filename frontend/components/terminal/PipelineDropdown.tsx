'use client'

import { useEffect, useState, useRef } from 'react'
import Link from 'next/link'
import type { PipelineStatus } from '@/types/pipeline'
import { getApiBaseUrl } from '@/config/api-config'

// =============================================================================
// HELPERS
// =============================================================================

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}m ${secs}s`
}

function formatTime(isoString: string | null): string {
  if (!isoString) return '—'
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

// =============================================================================
// MAIN COMPONENT
// =============================================================================

export function PipelineDropdown() {
  const [isOpen, setIsOpen] = useState(false)
  const [status, setStatus] = useState<PipelineStatus | null>(null)
  const [runningPipeline, setRunningPipeline] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Fetch pipeline status
  async function fetchStatus() {
    try {
      const res = await fetch(`${getApiBaseUrl()}/pipeline/status`)
      if (res.ok) {
        setStatus(await res.json())
      }
    } catch (error) {
      console.debug('Failed to fetch pipeline status:', error)
    }
  }

  useEffect(() => {
    fetchStatus()
    // Poll more frequently when running
    const interval = setInterval(fetchStatus, status?.running ? 2000 : 10000)
    return () => clearInterval(interval)
  }, [status?.running])

  // Sync local state with server state
  useEffect(() => {
    if (status?.running === false && runningPipeline) {
      setRunningPipeline(false)
    }
  }, [status?.running, runningPipeline])

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Run pipeline
  async function runPipeline(full: boolean = true, maxEvents?: number) {
    setRunningPipeline(true)
    try {
      const res = await fetch(`${getApiBaseUrl()}/pipeline/run/production`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ full, max_events: maxEvents }),
      })
      if (res.ok) {
        fetchStatus()
      } else {
        setRunningPipeline(false)
      }
    } catch (error) {
      console.error('Failed to run pipeline:', error)
      setRunningPipeline(false)
    }
  }

  const isRunning = runningPipeline || status?.running
  const stepProgress = status?.step_progress
  const completedSteps = stepProgress?.completed_count || 0
  const totalSteps = stepProgress?.total_steps || 8
  const progressPercent = totalSteps > 0 ? (completedSteps / totalSteps) * 100 : 0
  const currentStep = stepProgress?.current_step
  const lastRun = status?.production?.last_run

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Trigger Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg border transition-colors ${
          isRunning
            ? 'bg-cyan/10 border-cyan/30 text-cyan'
            : 'bg-surface-elevated border-border text-text-secondary hover:text-text-primary hover:border-text-muted'
        }`}
      >
        {/* Status indicator */}
        <span
          className={`w-2 h-2 rounded-full ${
            isRunning ? 'bg-cyan animate-pulse' : lastRun?.status === 'completed' ? 'bg-emerald' : 'bg-text-muted'
          }`}
        />

        <span className="text-xs font-medium">
          {isRunning ? 'Running...' : 'Pipeline'}
        </span>

        {/* Progress when running */}
        {isRunning && stepProgress && (
          <span className="text-[10px] font-mono">
            {completedSteps}/{totalSteps}
          </span>
        )}

        {/* Dropdown arrow */}
        <svg
          className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* Dropdown Panel */}
      {isOpen && (
        <div className="absolute right-0 top-full mt-1 w-72 bg-surface border border-border rounded-lg shadow-lg z-50 overflow-hidden">
          {/* Header */}
          <div className="px-3 py-2.5 border-b border-border bg-surface-elevated">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-text-primary">Pipeline Status</span>
              <Link
                href="/pipeline"
                className="text-[10px] text-text-muted hover:text-cyan transition-colors"
                onClick={() => setIsOpen(false)}
              >
                View details →
              </Link>
            </div>
          </div>

          {/* Content */}
          <div className="p-3 space-y-3">
            {/* Progress Section (when running) */}
            {isRunning && stepProgress && (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-text-muted">Progress</span>
                  <span className="font-mono text-cyan">
                    {completedSteps}/{totalSteps} steps
                  </span>
                </div>

                {/* Progress bar */}
                <div className="h-1.5 bg-surface-elevated rounded-full overflow-hidden">
                  <div
                    className="h-full bg-cyan rounded-full transition-all duration-300"
                    style={{ width: `${progressPercent}%` }}
                  />
                </div>

                {/* Current step */}
                {currentStep && (
                  <div className="flex items-center gap-2 text-xs">
                    {currentStep.emoji && <span>{currentStep.emoji}</span>}
                    <span className="text-text-secondary">{currentStep.step_name}</span>
                    <span className="text-text-muted font-mono ml-auto">
                      {formatElapsed(currentStep.elapsed_seconds)}
                    </span>
                  </div>
                )}
              </div>
            )}

            {/* Last Run Info (when not running) */}
            {!isRunning && lastRun && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-text-muted">Last run</span>
                  <span
                    className={`font-medium ${lastRun.status === 'completed' ? 'text-emerald' : 'text-rose'}`}
                  >
                    {lastRun.status}
                  </span>
                </div>
                <div className="flex items-center justify-between text-[10px] text-text-muted">
                  <span>{formatTime(lastRun.completed_at)}</span>
                  <span>
                    {lastRun.events_processed} events • {lastRun.new_events} new
                  </span>
                </div>
              </div>
            )}

            {/* No data state */}
            {!isRunning && !lastRun && (
              <p className="text-xs text-text-muted text-center py-2">
                No pipeline runs yet
              </p>
            )}

            {/* Actions */}
            <div className="pt-2 border-t border-border space-y-1.5">
              <button
                onClick={() => runPipeline(false, 50)}
                disabled={isRunning}
                className="w-full flex items-center justify-between px-2.5 py-1.5 rounded text-xs bg-surface-elevated hover:bg-surface-hover text-text-secondary hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <span>Quick Demo</span>
                <span className="text-[10px] text-text-muted">50 events</span>
              </button>
              <button
                onClick={() => runPipeline(false)}
                disabled={isRunning}
                className="w-full flex items-center justify-between px-2.5 py-1.5 rounded text-xs bg-surface-elevated hover:bg-surface-hover text-text-secondary hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <span>Add New Events</span>
                <span className="text-[10px] text-text-muted">incremental</span>
              </button>
              <button
                onClick={() => runPipeline(true)}
                disabled={isRunning}
                className="w-full px-2.5 py-1.5 rounded text-xs bg-cyan/10 hover:bg-cyan/20 text-cyan border border-cyan/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {isRunning ? 'Processing...' : 'Full Rebuild'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
