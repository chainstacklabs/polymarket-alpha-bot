/**
 * Unified Portfolio interface for hedge strategy data.
 *
 * Combines fields from both API/WebSocket responses and UI requirements.
 * Some fields are optional as they may not be present in all contexts.
 */
export interface Portfolio {
  pair_id: string

  // Target
  target_group_id: string
  target_group_title: string
  target_group_slug?: string
  target_market_id: string
  target_market_slug?: string
  target_question: string
  target_position: 'YES' | 'NO'
  target_price: number
  target_bracket?: string

  // Cover
  cover_group_id: string
  cover_group_title: string
  cover_group_slug?: string
  cover_market_id: string
  cover_market_slug?: string
  cover_question: string
  cover_position: 'YES' | 'NO'
  cover_price: number
  cover_bracket?: string
  cover_probability: number

  // Relationship
  relationship?: string
  relationship_type?: string

  // Metrics
  total_cost: number
  expected_fees?: number
  profit?: number
  profit_pct?: number
  coverage: number
  loss_probability: number
  expected_profit: number

  // Tier
  tier: number
  tier_label: string

  // Validation
  viability_score?: number
  validation_analysis?: string
}
