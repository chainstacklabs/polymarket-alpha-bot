import { useEffect, useCallback, RefObject } from 'react'

// =============================================================================
// TYPES
// =============================================================================

export interface KeyboardShortcutActions {
  // Navigation
  onNavigateUp: () => void
  onNavigateDown: () => void
  onSelect: () => void
  onClose: () => void

  // Filters
  onToggleProfitable: () => void

  // Actions
  onFocusSearch: () => void
  onRefresh: () => void
  onShowHelp: () => void
}

interface UseKeyboardShortcutsOptions {
  enabled?: boolean
  searchInputRef?: RefObject<HTMLInputElement | null>
}

// =============================================================================
// HOOK
// =============================================================================

export function useKeyboardShortcuts(
  actions: KeyboardShortcutActions,
  options: UseKeyboardShortcutsOptions = {}
) {
  const { enabled = true } = options

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      // Don't trigger shortcuts when typing in inputs
      const target = event.target as HTMLElement
      const isInputFocused =
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable

      // Allow Escape to work even in inputs (to blur/close)
      if (event.key === 'Escape') {
        if (isInputFocused) {
          ;(target as HTMLInputElement).blur()
        }
        actions.onClose()
        event.preventDefault()
        return
      }

      // Don't process other shortcuts when typing
      if (isInputFocused) return

      switch (event.key) {
        // Navigation
        case 'j':
        case 'ArrowDown':
          actions.onNavigateDown()
          event.preventDefault()
          break

        case 'k':
        case 'ArrowUp':
          actions.onNavigateUp()
          event.preventDefault()
          break

        case 'Enter':
          actions.onSelect()
          event.preventDefault()
          break

        // Toggles
        case 'p':
          actions.onToggleProfitable()
          event.preventDefault()
          break

        // Actions
        case '/':
          actions.onFocusSearch()
          event.preventDefault()
          break

        case 'r':
          // Only refresh if not holding modifiers (allow Cmd+R for browser refresh)
          if (!event.metaKey && !event.ctrlKey) {
            actions.onRefresh()
            event.preventDefault()
          }
          break

        case '?':
          actions.onShowHelp()
          event.preventDefault()
          break
      }
    },
    [actions]
  )

  useEffect(() => {
    if (!enabled) return

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [enabled, handleKeyDown])
}

// =============================================================================
// SHORTCUTS DATA (for help modal)
// =============================================================================

export const KEYBOARD_SHORTCUTS = [
  {
    category: 'Navigation',
    shortcuts: [
      { keys: ['j', '↓'], description: 'Move down' },
      { keys: ['k', '↑'], description: 'Move up' },
      { keys: ['Enter'], description: 'Open selected strategy' },
      { keys: ['Esc'], description: 'Close modal / blur search' },
    ],
  },
  {
    category: 'Filters',
    shortcuts: [{ keys: ['p'], description: 'Toggle profitable only' }],
  },
  {
    category: 'Actions',
    shortcuts: [
      { keys: ['/'], description: 'Focus search' },
      { keys: ['r'], description: 'Refresh data' },
      { keys: ['?'], description: 'Show this help' },
    ],
  },
]
