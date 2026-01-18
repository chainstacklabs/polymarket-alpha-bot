'use client'

import { useState, useRef, useEffect } from 'react'
import type { Portfolio } from '@/components/PortfolioModal'

interface ExportDropdownProps {
  portfolios: Portfolio[]
  filename?: string
}

export function ExportDropdown({ portfolios, filename = 'alphapoly-strategies' }: ExportDropdownProps) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const downloadFile = (content: string, type: string, extension: string) => {
    const blob = new Blob([content], { type })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${filename}-${new Date().toISOString().split('T')[0]}.${extension}`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    setIsOpen(false)
  }

  const exportCSV = () => {
    const headers = [
      'Tier',
      'Target Question',
      'Target Position',
      'Target Price',
      'Cover Question',
      'Cover Position',
      'Cover Price',
      'LLM Confidence',
      'Total Cost',
      'Expected Return',
      'Loss Probability',
    ]

    const rows = portfolios.map((p) => [
      p.tier_label,
      `"${p.target_question.replace(/"/g, '""')}"`,
      p.target_position,
      p.target_price.toFixed(2),
      `"${p.cover_question.replace(/"/g, '""')}"`,
      p.cover_position,
      p.cover_price.toFixed(2),
      (p.coverage * 100).toFixed(1) + '%',
      p.total_cost.toFixed(2),
      (p.expected_profit * 100).toFixed(1) + '%',
      (p.loss_probability * 100).toFixed(1) + '%',
    ])

    const csv = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n')
    downloadFile(csv, 'text/csv;charset=utf-8', 'csv')
  }

  const exportJSON = () => {
    const data = portfolios.map((p) => ({
      pair_id: p.pair_id,
      tier: p.tier,
      tier_label: p.tier_label,
      target: {
        question: p.target_question,
        group: p.target_group_title,
        position: p.target_position,
        price: p.target_price,
        slug: p.target_group_slug,
      },
      cover: {
        question: p.cover_question,
        group: p.cover_group_title,
        position: p.cover_position,
        price: p.cover_price,
        slug: p.cover_group_slug,
        probability: p.cover_probability,
      },
      metrics: {
        llm_confidence: p.coverage,
        total_cost: p.total_cost,
        expected_profit: p.expected_profit,
        loss_probability: p.loss_probability,
      },
    }))

    const json = JSON.stringify({ exported_at: new Date().toISOString(), strategies: data }, null, 2)
    downloadFile(json, 'application/json', 'json')
  }

  if (portfolios.length === 0) {
    return null
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 px-2 py-1 rounded border border-border bg-surface-elevated hover:bg-surface-hover text-text-muted hover:text-text-secondary transition-colors"
        title="Export strategies"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"
          />
        </svg>
        <span className="text-xs">Export</span>
        <svg
          className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-1 w-48 bg-surface border border-border rounded-lg shadow-lg z-50 overflow-hidden">
          <div className="p-1.5">
            <button
              onClick={exportCSV}
              className="w-full flex items-center gap-2 px-2.5 py-2 rounded text-left text-sm text-text-secondary hover:text-text-primary hover:bg-surface-hover transition-colors"
            >
              <svg className="w-4 h-4 text-emerald" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
              <div>
                <p className="font-medium">CSV</p>
                <p className="text-[10px] text-text-muted">Spreadsheet format</p>
              </div>
            </button>

            <button
              onClick={exportJSON}
              className="w-full flex items-center gap-2 px-2.5 py-2 rounded text-left text-sm text-text-secondary hover:text-text-primary hover:bg-surface-hover transition-colors"
            >
              <svg className="w-4 h-4 text-cyan" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"
                />
              </svg>
              <div>
                <p className="font-medium">JSON</p>
                <p className="text-[10px] text-text-muted">Developer format</p>
              </div>
            </button>
          </div>

          <div className="px-3 py-2 border-t border-border bg-surface-elevated">
            <p className="text-[10px] text-text-muted">
              Exporting {portfolios.length} strategies
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
