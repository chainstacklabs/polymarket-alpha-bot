export interface StepProgress {
  step_number: number
  step_name: string
  status: 'running' | 'completed' | 'failed'
  started_at: string
  elapsed_seconds: number
  details: string | null
}

export interface StepProgressData {
  current_step: StepProgress | null
  completed_steps: StepProgress[]
  pipeline_elapsed_seconds: number
  total_steps: number
  completed_count: number
}

export interface PipelineStatus {
  timestamp: string
  running: boolean
  current_step: string | null
  step_progress: StepProgressData | null
}
