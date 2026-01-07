interface StatusCardProps {
  title: string
  value: string
  subtitle: string
  status: 'success' | 'warning' | 'error' | 'info'
  icon?: React.ReactNode
}

const statusConfig = {
  success: {
    accent: 'border-l-emerald',
    text: 'text-emerald',
  },
  warning: {
    accent: 'border-l-amber',
    text: 'text-amber',
  },
  error: {
    accent: 'border-l-rose',
    text: 'text-rose',
  },
  info: {
    accent: 'border-l-cyan',
    text: 'text-cyan',
  },
}

export function StatusCard({ title, value, subtitle, status }: StatusCardProps) {
  const config = statusConfig[status]

  return (
    <div
      className={`
        bg-surface border border-border rounded-lg p-4
        border-l-2 ${config.accent}
        transition-colors hover:bg-surface-hover
      `}
    >
      {/* Title */}
      <p className="text-[11px] uppercase tracking-wider text-text-muted font-medium mb-2">
        {title}
      </p>

      {/* Value */}
      <p className={`text-2xl font-semibold tracking-tight ${config.text}`}>
        {value}
      </p>

      {/* Subtitle */}
      <p className="text-xs text-text-muted mt-1">{subtitle}</p>
    </div>
  )
}
