// =============================================================================
// API CONFIGURATION - Dynamic URL resolution for backend connections
// =============================================================================

/**
 * Get the base URL for API requests.
 * Uses Next.js API proxy (/api) for client-side to avoid CORS issues.
 * Server-side uses direct connection.
 */
export function getApiBaseUrl(): string {
  if (typeof window === 'undefined') {
    // Server-side rendering - direct connection
    return 'http://localhost:8000'
  }

  // Client-side - use Next.js API proxy to avoid CORS/Safari issues
  return '/api'
}

/**
 * Get the WebSocket URL for portfolio price updates.
 * Uses same-origin proxy path to avoid CORS/Safari issues.
 */
export function getPortfolioWsUrl(): string {
  if (typeof window === 'undefined') {
    return 'ws://localhost:8000/portfolios/ws'
  }

  // Use same-origin WebSocket via Next.js proxy
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/portfolios/ws`
}

/**
 * Get the WebSocket URL for price updates.
 * Uses same-origin proxy path to avoid CORS/Safari issues.
 */
export function getPricesWsUrl(): string {
  if (typeof window === 'undefined') {
    return 'ws://localhost:8000/prices/ws'
  }

  // Use same-origin WebSocket via Next.js proxy
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/prices/ws`
}

/**
 * Get the API docs URL.
 */
export function getApiDocsUrl(): string {
  if (typeof window === 'undefined') {
    return 'http://localhost:8000/docs'
  }

  const hostname = window.location.hostname

  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return 'http://localhost:8000/docs'
  }

  return `http://${hostname}:8000/docs`
}
