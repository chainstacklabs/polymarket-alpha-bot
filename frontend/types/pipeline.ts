export interface StepProgress {
  step_number: number
  step_name: string
  status: 'running' | 'completed' | 'failed'
  started_at: string
  elapsed_seconds: number
  details: string | null
  description: string | null
  emoji: string | null
}

export interface StepProgressData {
  current_step: StepProgress | null
  completed_steps: StepProgress[]
  pipeline_elapsed_seconds: number
  total_steps: number
  completed_count: number
}

export interface LastRun {
  id: number
  run_type: string
  started_at: string
  completed_at: string | null
  events_processed: number | null
  new_events: number | null
  status: string
}

export interface ProductionState {
  total_events: number
  total_entities: number
  total_edges: number
  last_full_run: string | null
  last_refresh: string | null
  last_run: LastRun | null
}

export interface PipelineStatus {
  timestamp: string
  running: boolean
  current_step: string | null
  step_progress: StepProgressData | null
  production: ProductionState | null
}
