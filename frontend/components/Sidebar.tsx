'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { getApiDocsUrl } from '@/config/api-config'

const getNavigation = () => [
  { name: 'Terminal', href: '/terminal' },
  { name: 'Positions', href: '/positions' },
  { name: 'API Docs', href: getApiDocsUrl(), external: true },
]

export function Sidebar() {
  const pathname = usePathname()
  const navigation = getNavigation()

  return (
    <div className="fixed inset-y-0 left-0 w-48 bg-surface border-r border-border flex flex-col">
      {/* Brand */}
      <div className="px-4 py-5 border-b border-border">
        <Link href="/terminal" className="block group">
          <span className="text-base font-semibold tracking-tight text-text-primary group-hover:text-cyan transition-colors">
            alphapoly
          </span>
          <p className="text-[10px] text-text-muted mt-0.5">
            Smart Hedging Strategies
          </p>
        </Link>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {navigation.map((item) => {
          const isActive = pathname === item.href
          const isExternal = 'external' in item && item.external

          if (isExternal) {
            return (
              <a
                key={item.name}
                href={item.href}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center justify-between px-3 py-2 rounded text-sm transition-colors text-text-secondary hover:text-text-primary hover:bg-surface-hover"
              >
                {item.name}
                <span className="text-[10px] text-text-muted">↗</span>
              </a>
            )
          }

          return (
            <Link
              key={item.name}
              href={item.href}
              className={`
                block px-3 py-2 rounded text-sm transition-colors
                ${
                  isActive
                    ? 'bg-surface-elevated text-text-primary border-l-2 border-cyan -ml-px pl-[11px]'
                    : 'text-text-secondary hover:text-text-primary hover:bg-surface-hover'
                }
              `}
            >
              {item.name}
            </Link>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald" />
            <span className="text-xs text-text-muted">Live</span>
          </div>
          <span className="text-[10px] text-text-muted font-mono">v1.0</span>
        </div>
      </div>
    </div>
  )
}
