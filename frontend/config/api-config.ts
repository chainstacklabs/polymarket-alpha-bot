// =============================================================================
// API CONFIGURATION - Dynamic URL resolution for backend connections
// =============================================================================

/**
 * Get the base URL for API requests.
 * Dynamically uses the current hostname to support remote access (e.g., OrbStack).
 */
export function getApiBaseUrl(): string {
  if (typeof window === 'undefined') {
    // Server-side rendering - use localhost
    return 'http://localhost:8000'
  }

  const hostname = window.location.hostname

  // If accessing from localhost, use localhost
  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return 'http://localhost:8000'
  }

  // Otherwise, use the same hostname with backend port
  return `http://${hostname}:8000`
}

/**
 * Get the WebSocket URL for portfolio price updates.
 */
export function getPortfolioWsUrl(): string {
  if (typeof window === 'undefined') {
    return 'ws://localhost:8000/portfolios/ws'
  }

  const hostname = window.location.hostname

  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return 'ws://localhost:8000/portfolios/ws'
  }

  return `ws://${hostname}:8000/portfolios/ws`
}

/**
 * Get the WebSocket URL for price updates.
 */
export function getPricesWsUrl(): string {
  if (typeof window === 'undefined') {
    return 'ws://localhost:8000/prices/ws'
  }

  const hostname = window.location.hostname

  if (hostname === 'localhost' || hostname === '127.0.0.1') {
    return 'ws://localhost:8000/prices/ws'
  }

  return `ws://${hostname}:8000/prices/ws`
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
