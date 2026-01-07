// Relation type explanations for tooltips
const RELATION_HINTS: Record<string, string> = {
  'DIRECT_CAUSE': 'A directly causes B to happen (high probability)',
  'ENABLING_CONDITION': 'A makes B more likely, but doesn\'t guarantee it',
  'INHIBITING_CONDITION': 'A reduces the likelihood of B happening',
  'REQUIRES': 'B cannot happen unless A happens first',
  'CORRELATED': 'A and B tend to move together, but unclear which causes which',
  'TIMEFRAME_VARIANT': 'Same event with different time deadlines',
  'THRESHOLD_VARIANT': 'Same event with different numeric thresholds',
  'MUTUALLY_EXCLUSIVE': 'If A happens, B cannot happen (and vice versa)',
}

const getRelationHint = (type: string): string => {
  const normalized = type.toUpperCase().replace(/\s+/g, '_')
  return RELATION_HINTS[normalized] || type
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

interface OpportunityCardProps {
  opportunity: Opportunity
  currentPrice?: number
}

export function OpportunityCard({ opportunity, currentPrice }: OpportunityCardProps) {
  const { trigger, consequence, relation, alpha } = opportunity

  const priceChange = currentPrice !== undefined
    ? ((currentPrice - consequence.price) / consequence.price) * 100
    : null

  const isBuy = alpha.direction === 'BUY'

  return (
    <div
      className={`
        bg-surface border border-border rounded-lg p-4
        border-l-2 ${isBuy ? 'border-l-alpha-buy' : 'border-l-alpha-sell'}
        transition-colors hover:bg-surface-hover
      `}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-text-muted">#{opportunity.rank}</span>
          <span
            className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-surface-elevated text-text-muted border border-border cursor-help"
            title={getRelationHint(relation.type)}
          >
            {relation.type}
          </span>
        </div>
        <span
          className={`
            text-xs font-semibold font-mono
            ${isBuy ? 'text-alpha-buy' : 'text-alpha-sell'}
          `}
        >
          {alpha.direction} {alpha.signal_display}
        </span>
      </div>

      {/* Events */}
      <div className="space-y-2">
        {/* Trigger */}
        <div className="flex items-start gap-2">
          <span className="text-[10px] font-medium text-text-muted w-8 pt-0.5">IF</span>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-text-primary truncate" title={trigger.title}>
              {trigger.title}
            </p>
            <span className="text-xs font-mono text-text-muted">{trigger.price_display}</span>
          </div>
        </div>

        {/* Consequence */}
        <div className="flex items-start gap-2">
          <span className="text-[10px] font-medium text-text-muted w-8 pt-0.5">THEN</span>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-text-primary truncate" title={consequence.title}>
              {consequence.title}
            </p>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-text-muted">{consequence.price_display}</span>
              {priceChange !== null && (
                <span
                  className={`
                    text-[10px] font-mono font-medium
                    ${priceChange > 0 ? 'text-alpha-buy' : priceChange < 0 ? 'text-alpha-sell' : 'text-text-muted'}
                  `}
                >
                  {priceChange > 0 ? '+' : ''}{priceChange.toFixed(0)}%
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between mt-3 pt-3 border-t border-border">
        <div className="flex items-center gap-2">
          <div className="w-12 h-1 bg-surface-elevated rounded-full overflow-hidden">
            <div
              className="h-full bg-cyan rounded-full"
              style={{ width: `${relation.confidence * 100}%` }}
            />
          </div>
          <span className="text-[10px] font-mono text-text-muted">
            {(relation.confidence * 100).toFixed(0)}%
          </span>
        </div>
        <button
          onClick={() => window.open(`https://polymarket.com/event/${consequence.event_id}`, '_blank')}
          className="text-xs text-text-secondary hover:text-cyan transition-colors"
        >
          View →
        </button>
      </div>
    </div>
  )
}
