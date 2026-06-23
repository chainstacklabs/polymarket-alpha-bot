'use client'

import { useEffect, useState, useRef } from 'react'
import type { PipelineStatus } from '@/types/pipeline'
import { getApiBaseUrl } from '@/config/api-config'
import { formatElapsed, formatTime } from '@/utils/format-time'
import { useModelSettings } from '@/hooks/useModelSettings'

// =============================================================================
// MAIN COMPONENT
// =============================================================================

export function PipelineDropdown() {
  const [isOpen, setIsOpen] = useState(false)
  const [status, setStatus] = useState<PipelineStatus | null>(null)
  const [runningPipeline, setRunningPipeline] = useState(false)
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [resetting, setResetting] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const { settings: modelSettings, update: updateModelSettings } =
    useModelSettings()

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
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
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
        body: JSON.stringify({
          full,
          max_events: maxEvents,
          implications_model: modelSettings.implicationsModel || undefined,
          validation_model: modelSettings.validationModel || undefined,
          tags: modelSettings.tags.trim() || undefined,
        }),
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

  // Reset pipeline state
  async function resetPipeline() {
    setResetting(true)
    try {
      const res = await fetch(`${getApiBaseUrl()}/pipeline/reset`, {
        method: 'POST',
      })
      if (res.ok) {
        fetchStatus()
      }
    } catch (error) {
      console.error('Failed to reset pipeline:', error)
    } finally {
      setResetting(false)
      setShowResetConfirm(false)
    }
  }

  const isRunning = runningPipeline || status?.running
  const stepProgress = status?.step_progress
  const completedSteps = stepProgress?.completed_count || 0
  const totalSteps = stepProgress?.total_steps || 8
  const progressPercent =
    totalSteps > 0 ? (completedSteps / totalSteps) * 100 : 0
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
            isRunning
              ? 'bg-cyan animate-pulse'
              : lastRun?.status === 'completed'
                ? 'bg-emerald'
                : 'bg-text-muted'
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
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 9l-7 7-7-7"
          />
        </svg>
      </button>

      {/* Dropdown Panel */}
      {isOpen && (
        <div className="absolute right-0 top-full mt-1 w-72 bg-surface border border-border rounded-lg shadow-lg z-50 overflow-hidden">
          {/* Header */}
          <div className="px-3 py-2.5 border-b border-border bg-surface-elevated">
            <span className="text-xs font-medium text-text-primary">
              Pipeline Status
            </span>
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
                {currentStep &&
                  (() => {
                    const activeModel =
                      currentStep.step_number === 4
                        ? modelSettings.implicationsModel ||
                          status?.default_models?.implications ||
                          ''
                        : currentStep.step_number === 6
                          ? modelSettings.validationModel ||
                            status?.default_models?.validation ||
                            ''
                          : ''
                    return (
                      <div className="flex items-center gap-2 text-xs">
                        {currentStep.emoji && <span>{currentStep.emoji}</span>}
                        <span className="text-text-secondary">
                          {currentStep.step_name}
                        </span>
                        {activeModel && (
                          <span
                            className="text-[9px] text-cyan/60 truncate max-w-[100px]"
                            title={activeModel}
                          >
                            {activeModel.split('/').pop()}
                          </span>
                        )}
                        <span className="text-text-muted font-mono ml-auto">
                          {formatElapsed(currentStep.elapsed_seconds)}
                        </span>
                      </div>
                    )
                  })()}
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

            {/* Error banner (last run failed) */}
            {!isRunning && (status?.error || lastRun?.status === 'failed') && (
              <div className="rounded border border-rose/30 bg-rose/10 px-2.5 py-2 space-y-1">
                <div className="flex items-center gap-1.5 text-xs font-medium text-rose">
                  <span>⚠</span>
                  <span>Pipeline failed</span>
                </div>
                <p className="text-[10px] text-rose/90 leading-tight break-words">
                  {status?.error ||
                    'The last run failed. Check the backend logs for details.'}
                </p>
              </div>
            )}

            {/* No data state */}
            {!isRunning && !lastRun && !status?.error && (
              <p className="text-xs text-text-muted text-center py-2">
                No pipeline runs yet
              </p>
            )}

            {/* Actions */}
            <div className="pt-2 border-t border-border space-y-1.5">
              {/* LLM cost warning */}
              <p className="text-[10px] text-amber-400/80 leading-tight pb-1.5">
                ⚠ Pipeline uses LLM tokens. Try Quick Demo or free models first.
                Free/cheap models may miss opportunities. Budget spend on LLM
                can exceed hedge profits.
              </p>
              <button
                onClick={() => runPipeline(false, 50)}
                disabled={isRunning || resetting}
                className="w-full flex items-center justify-between px-2.5 py-1.5 rounded text-xs bg-surface-elevated hover:bg-surface-hover text-text-secondary hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <span>Quick Demo</span>
                <span className="text-[10px] text-text-muted">50 events</span>
              </button>
              <button
                onClick={() => runPipeline(false)}
                disabled={isRunning || resetting}
                className="w-full flex items-center justify-between px-2.5 py-1.5 rounded text-xs bg-surface-elevated hover:bg-surface-hover text-text-secondary hover:text-text-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                <span>Add New Events</span>
                <span className="text-[10px] text-text-muted">incremental</span>
              </button>
              <button
                onClick={() => runPipeline(true)}
                disabled={isRunning || resetting}
                className="w-full px-2.5 py-1.5 rounded text-xs bg-cyan/10 hover:bg-cyan/20 text-cyan border border-cyan/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {isRunning ? 'Processing...' : 'Full Rebuild'}
              </button>
            </div>

            {/* Reset Section */}
            <div className="pt-2 border-t border-border">
              {!showResetConfirm ? (
                <button
                  onClick={() => setShowResetConfirm(true)}
                  disabled={isRunning || resetting}
                  className="w-full px-2.5 py-1.5 rounded text-xs text-text-muted hover:text-rose hover:bg-rose/10 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  Reset All Data
                </button>
              ) : (
                <div className="space-y-1.5">
                  <p className="text-[10px] text-text-muted text-center">
                    This will clear all pipeline data
                  </p>
                  <div className="flex gap-1.5">
                    <button
                      onClick={() => setShowResetConfirm(false)}
                      disabled={resetting}
                      className="flex-1 px-2 py-1 rounded text-xs bg-surface-elevated hover:bg-surface-hover text-text-secondary disabled:opacity-50 transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={resetPipeline}
                      disabled={resetting}
                      className="flex-1 px-2 py-1 rounded text-xs bg-rose/10 hover:bg-rose/20 text-rose border border-rose/30 disabled:opacity-50 transition-colors"
                    >
                      {resetting ? 'Resetting...' : 'Confirm'}
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Market Tags */}
            <div className="pt-2 border-t border-border space-y-1.5">
              <span className="text-[10px] font-medium text-text-muted uppercase tracking-wider">
                Market Tags
              </span>
              <input
                type="text"
                value={modelSettings.tags}
                onChange={(e) => updateModelSettings({ tags: e.target.value })}
                placeholder={status?.default_tag || 'politics'}
                className="w-full px-2 py-1 rounded text-xs bg-surface-elevated border border-border text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-cyan/50"
              />
              <p className="text-[9px] text-text-muted leading-tight">
                Polymarket tag slugs. Separate multiple with{' '}
                <span className="text-text-secondary">,</span> or{' '}
                <span className="text-text-secondary">;</span> (e.g.{' '}
                <span className="text-text-secondary">politics, crypto</span>).
                Leave blank for the default.
              </p>
            </div>

            {/* LLM Models */}
            <div className="pt-2 border-t border-border space-y-2">
              <span className="text-[10px] font-medium text-text-muted uppercase tracking-wider">
                LLM Models
              </span>
              <div className="space-y-1.5">
                <div>
                  <label className="text-[10px] text-text-muted block mb-0.5">
                    Implications
                  </label>
                  <input
                    type="text"
                    value={modelSettings.implicationsModel}
                    onChange={(e) =>
                      updateModelSettings({
                        implicationsModel: e.target.value,
                      })
                    }
                    placeholder={
                      status?.default_models?.implications || 'env default'
                    }
                    className="w-full px-2 py-1 rounded text-xs bg-surface-elevated border border-border text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-cyan/50"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-text-muted block mb-0.5">
                    Validation
                  </label>
                  <input
                    type="text"
                    value={modelSettings.validationModel}
                    onChange={(e) =>
                      updateModelSettings({
                        validationModel: e.target.value,
                      })
                    }
                    placeholder={
                      status?.default_models?.validation || 'env default'
                    }
                    className="w-full px-2 py-1 rounded text-xs bg-surface-elevated border border-border text-text-primary placeholder:text-text-muted/50 focus:outline-none focus:border-cyan/50"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
