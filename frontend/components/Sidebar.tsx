'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const navigation = [
  { name: 'Dashboard', href: '/' },
  { name: 'Opportunities', href: '/opportunities' },
  { name: 'Pipeline', href: '/pipeline' },
]

export function Sidebar() {
  const pathname = usePathname()

  return (
    <div className="fixed inset-y-0 left-0 w-48 bg-surface border-r border-border flex flex-col">
      {/* Brand */}
      <div className="px-5 py-5 border-b border-border">
        <Link href="/" className="block">
          <span className="text-lg font-semibold tracking-tight text-text-primary">
            alphapoly
          </span>
        </Link>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {navigation.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.name}
              href={item.href}
              className={`
                block px-3 py-2 rounded text-sm transition-colors
                ${isActive
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
      <div className="px-5 py-4 border-t border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald" />
            <span className="text-xs text-text-muted">Live</span>
          </div>
          <span className="text-[10px] text-text-muted font-mono">v0.1</span>
        </div>
      </div>
    </div>
  )
}
