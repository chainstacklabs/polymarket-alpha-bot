'use client'

import { useState } from 'react'
import type { StepProgress, StepProgressData } from '@/types/pipeline'

interface PipelineTimelineProps {
  stepProgress: StepProgressData | null
  isRunning: boolean
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}m ${secs}s`
}

function getStatusColor(status: string): string {
  switch (status) {
    case 'running':
      return 'text-cyan'
    case 'completed':
      return 'text-emerald'
    case 'failed':
      return 'text-rose'
    default:
      return 'text-text-muted'
  }
}

function getStatusDot(status: string): string {
  switch (status) {
    case 'running':
      return 'bg-cyan animate-pulse'
    case 'completed':
      return 'bg-emerald'
    case 'failed':
      return 'bg-rose'
    default:
      return 'bg-surface-elevated'
  }
}

function StepCard({
  step,
  isExpanded,
  onToggle,
  isLast,
  totalSteps,
}: {
  step: StepProgress
  isExpanded: boolean
  onToggle: () => void
  isLast: boolean
  totalSteps: number
}) {
  return (
    <div className="flex gap-3">
      {/* Timeline indicator */}
      <div className="flex flex-col items-center">
        <div
          className={`w-2.5 h-2.5 rounded-full mt-1.5 ${getStatusDot(step.status)}`}
        />
        {!isLast && <div className="w-px flex-1 bg-border mt-1" />}
      </div>

      {/* Step content */}
      <div className="flex-1 pb-4">
        <button
          onClick={onToggle}
          className="w-full text-left group"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {step.emoji && (
                <span className="text-sm">{step.emoji}</span>
              )}
              <span className="text-xs font-mono text-text-muted">
                [{step.step_number}/{totalSteps}]
              </span>
              <span className="text-sm font-medium text-text-primary group-hover:text-cyan transition-colors">
                {step.step_name}
              </span>
              <span className={`text-xs ${getStatusColor(step.status)}`}>
                {step.status === 'running' && (
                  <span className="inline-flex items-center gap-1">
                    <span className="w-1 h-1 rounded-full bg-cyan animate-pulse" />
                    running
                  </span>
                )}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-text-muted">
                {formatElapsed(step.elapsed_seconds)}
              </span>
              <svg
                className={`w-4 h-4 text-text-muted transition-transform ${
                  isExpanded ? 'rotate-180' : ''
                }`}
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
            </div>
          </div>

          {/* Step description - always shown */}
          {step.description && (
            <p className="text-xs text-text-muted mt-1 ml-8">{step.description}</p>
          )}

          {/* Runtime details - shown below description */}
          {step.details && (
            <p className="text-xs text-emerald mt-0.5 ml-8">{step.details}</p>
          )}
        </button>

        {/* Expandable details */}
        {isExpanded && (
          <div className="mt-2 ml-7 p-3 rounded-lg bg-surface-elevated border border-border">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <p className="text-text-muted mb-0.5">Status</p>
                <p className={`font-medium ${getStatusColor(step.status)}`}>
                  {step.status.toUpperCase()}
                </p>
              </div>
              <div>
                <p className="text-text-muted mb-0.5">Duration</p>
                <p className="font-mono text-text-primary">
                  {formatElapsed(step.elapsed_seconds)}
                </p>
              </div>
              {step.started_at && (
                <div className="col-span-2">
                  <p className="text-text-muted mb-0.5">Started</p>
                  <p className="font-mono text-text-primary">
                    {new Date(step.started_at).toLocaleTimeString()}
                  </p>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function PipelineTimeline({
  stepProgress,
  isRunning,
}: PipelineTimelineProps) {
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set())

  if (!stepProgress && !isRunning) {
    return null
  }

  const toggleStep = (stepNumber: number) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev)
      if (next.has(stepNumber)) {
        next.delete(stepNumber)
      } else {
        next.add(stepNumber)
      }
      return next
    })
  }

  // Combine completed steps + current step
  const allSteps = [
    ...(stepProgress?.completed_steps || []),
    ...(stepProgress?.current_step ? [stepProgress.current_step] : []),
  ].sort((a, b) => a.step_number - b.step_number)

  const progressPercent =
    stepProgress && stepProgress.total_steps > 0
      ? (stepProgress.completed_count / stepProgress.total_steps) * 100
      : 0

  return (
    <div className="rounded-lg border border-border bg-surface overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border bg-surface-elevated">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-text-primary">
            {isRunning ? 'Processing...' : 'Previous Run'}
          </h3>
          {stepProgress && (
            <span className="text-xs text-text-muted font-mono">
              {formatElapsed(stepProgress.pipeline_elapsed_seconds)}
            </span>
          )}
        </div>

        {/* Overall progress bar */}
        {stepProgress && (
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-text-muted">
              <span>
                {stepProgress.completed_count}/{stepProgress.total_steps} steps
                {!isRunning && stepProgress.completed_count === stepProgress.total_steps && (
                  <span className="ml-2 text-emerald">Complete</span>
                )}
              </span>
              <span>{Math.round(progressPercent)}%</span>
            </div>
            <div className="h-1.5 bg-surface rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-300 ${
                  isRunning ? 'bg-cyan' : 'bg-emerald'
                }`}
                style={{ width: `${progressPercent}%` }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Steps list */}
      <div className="p-4">
        {allSteps.length > 0 ? (
          allSteps.map((step, idx) => (
            <StepCard
              key={step.step_number}
              step={step}
              isExpanded={expandedSteps.has(step.step_number)}
              onToggle={() => toggleStep(step.step_number)}
              isLast={idx === allSteps.length - 1}
              totalSteps={stepProgress?.total_steps || 8}
            />
          ))
        ) : (
          <div className="text-center py-4 text-sm text-text-muted">
            {isRunning ? 'Starting...' : 'Click a button above to start processing'}
          </div>
        )}
      </div>
    </div>
  )
}
