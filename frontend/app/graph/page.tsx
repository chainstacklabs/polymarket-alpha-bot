'use client'

import { useEffect, useState, useRef } from 'react'
import dynamic from 'next/dynamic'
import type cytoscape from 'cytoscape'
import type { ElementDefinition } from 'cytoscape'

// Dynamically import Cytoscape to avoid SSR issues
const CytoscapeComponent = dynamic(() => import('react-cytoscapejs'), {
  ssr: false,
  loading: () => <div className="flex items-center justify-center h-full text-gray-400">Loading graph...</div>
})

// Helper to normalize elements to flat array format
function normalizeElements(elements: { nodes: Array<{ data: Record<string, unknown> }>, edges: Array<{ data: Record<string, unknown> }> }): ElementDefinition[] {
  return [
    ...elements.nodes.map(n => ({ data: n.data })),
    ...elements.edges.map(e => ({ data: e.data })),
  ] as ElementDefinition[]
}

interface GraphData {
  elements: {
    nodes: Array<{
      data: {
        id: string
        label: string
        fullTitle?: string
        price?: number
        priceDisplay?: string
        hasAlpha?: boolean
        alphaCount?: number
        nodeType?: string
      }
    }>
    edges: Array<{
      data: {
        id: string
        source: string
        target: string
        relation: string
        confidence?: number
      }
    }>
  }
}

const relationColors: Record<string, string> = {
  REQUIRES: '#ef4444',
  DIRECT_CAUSE: '#f97316',
  ENABLING_CONDITION: '#eab308',
  INHIBITING_CONDITION: '#8b5cf6',
  CORRELATED: '#3b82f6',
  SERIES_MEMBER: '#10b981',
  THRESHOLD_VARIANT: '#6366f1',
  HIERARCHICAL: '#ec4899',
  DEFAULT: '#6b7280',
}

const cytoscapeStyles: cytoscape.StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      'background-color': '#374151',
      'label': 'data(label)',
      'color': '#d1d5db',
      'font-size': '10px',
      'text-valign': 'bottom' as const,
      'text-margin-y': 5,
      'width': 30,
      'height': 30,
      'border-width': 2,
      'border-color': '#4b5563',
    },
  },
  {
    selector: 'node[hasAlpha]',
    style: {
      'background-color': '#22c55e',
      'border-color': '#16a34a',
    },
  },
  {
    selector: 'node:selected',
    style: {
      'background-color': '#3b82f6',
      'border-color': '#2563eb',
      'border-width': 3,
    },
  },
  {
    selector: 'edge',
    style: {
      'width': 2,
      'line-color': '#4b5563',
      'target-arrow-color': '#4b5563',
      'target-arrow-shape': 'triangle' as const,
      'curve-style': 'bezier' as const,
      'opacity': 0.7,
    },
  },
  {
    selector: 'edge[relation = "REQUIRES"]',
    style: { 'line-color': '#ef4444', 'target-arrow-color': '#ef4444' },
  },
  {
    selector: 'edge[relation = "DIRECT_CAUSE"]',
    style: { 'line-color': '#f97316', 'target-arrow-color': '#f97316' },
  },
  {
    selector: 'edge[relation = "ENABLING_CONDITION"]',
    style: { 'line-color': '#eab308', 'target-arrow-color': '#eab308' },
  },
  {
    selector: 'edge[relation = "CORRELATED"]',
    style: { 'line-color': '#3b82f6', 'target-arrow-color': '#3b82f6' },
  },
  {
    selector: 'edge:selected',
    style: {
      'width': 4,
      'opacity': 1,
    },
  },
]

export default function GraphPage() {
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedNode, setSelectedNode] = useState<any>(null)
  const [filter, setFilter] = useState<string>('all')
  const cyRef = useRef<any>(null)

  useEffect(() => {
    async function fetchGraph() {
      try {
        const res = await fetch('http://localhost:8000/data/graph')
        if (res.ok) {
          const data = await res.json()
          setGraphData(data.data)
        }
      } catch (error) {
        console.error('Failed to fetch graph:', error)
      } finally {
        setLoading(false)
      }
    }

    fetchGraph()
  }, [])

  const handleCyReady = (cy: any) => {
    cyRef.current = cy

    cy.on('tap', 'node', (evt: any) => {
      const node = evt.target
      setSelectedNode(node.data())
    })

    cy.on('tap', (evt: any) => {
      if (evt.target === cy) {
        setSelectedNode(null)
      }
    })

    // Run layout after mount
    cy.layout({
      name: 'cose',
      animate: false,
      nodeRepulsion: 8000,
      idealEdgeLength: 100,
      edgeElasticity: 0.1,
    }).run()
  }

  const filteredElements = graphData?.elements ? {
    nodes: graphData.elements.nodes,
    edges: filter === 'all'
      ? graphData.elements.edges
      : graphData.elements.edges.filter(e => e.data.relation === filter),
  } : { nodes: [], edges: [] }

  const relationTypes = graphData?.elements?.edges
    ? Array.from(new Set(graphData.elements.edges.map(e => e.data.relation).filter(Boolean)))
    : []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Event Graph</h1>
          <p className="text-gray-400 mt-1">
            {graphData?.elements?.nodes?.length || 0} events, {graphData?.elements?.edges?.length || 0} relations
          </p>
        </div>
        <div className="flex items-center gap-4">
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm focus:outline-none focus:border-blue-500"
          >
            <option value="all">All Relations</option>
            {relationTypes.map(type => (
              <option key={type} value={type}>{type}</option>
            ))}
          </select>
          <button
            onClick={() => {
              cyRef.current?.fit()
            }}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-sm"
          >
            Fit View
          </button>
          <button
            onClick={() => {
              cyRef.current?.layout({
                name: 'cose',
                animate: true,
                animationDuration: 1000,
              }).run()
            }}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm"
          >
            Re-layout
          </button>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3">
        {Object.entries(relationColors).slice(0, -1).map(([type, color]) => (
          <div key={type} className="flex items-center gap-2 text-xs">
            <div className="w-3 h-3 rounded" style={{ backgroundColor: color }} />
            <span className="text-gray-400">{type}</span>
          </div>
        ))}
      </div>

      {/* Graph Container */}
      <div className="flex gap-6">
        <div className="flex-1 h-[600px] bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center h-full text-gray-400">
              Loading graph...
            </div>
          ) : graphData ? (
            <CytoscapeComponent
              elements={normalizeElements(filteredElements)}
              stylesheet={cytoscapeStyles}
              style={{ width: '100%', height: '100%' }}
              cy={handleCyReady}
              wheelSensitivity={0.2}
            />
          ) : (
            <div className="flex items-center justify-center h-full text-gray-400">
              No graph data available. Run the pipeline first.
            </div>
          )}
        </div>

        {/* Node Details Panel */}
        {selectedNode && (
          <div className="w-80 bg-gray-800 rounded-lg border border-gray-700 p-4">
            <h3 className="text-lg font-semibold mb-4">Event Details</h3>
            <div className="space-y-3 text-sm">
              <div>
                <span className="text-gray-400">Title:</span>
                <p className="mt-1">{selectedNode.fullTitle || selectedNode.label}</p>
              </div>
              <div>
                <span className="text-gray-400">ID:</span>
                <p className="mt-1 font-mono text-xs">{selectedNode.id}</p>
              </div>
              {selectedNode.priceDisplay && (
                <div>
                  <span className="text-gray-400">Price:</span>
                  <p className="mt-1 text-green-400">{selectedNode.priceDisplay}</p>
                </div>
              )}
              {selectedNode.hasAlpha && (
                <div>
                  <span className="text-gray-400">Alpha Opportunities:</span>
                  <p className="mt-1 text-green-400">{selectedNode.alphaCount}</p>
                </div>
              )}
              <button
                onClick={() => window.open(`https://polymarket.com/event/${selectedNode.id}`, '_blank')}
                className="w-full mt-4 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm"
              >
                View on Polymarket
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
