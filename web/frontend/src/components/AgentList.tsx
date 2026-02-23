import { useState } from 'react'
import type { GraphEdge, GraphNode, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
  selectedNode: string | null
  names: Record<string, string>
  onSelect: (id: string) => void
}

function roleLabel(id: string): string {
  if (id.startsWith('off_')) return 'Чиновник'
  if (id.startsWith('biz_')) return 'Подрядчик'
  if (id.startsWith('aud_')) return 'Аудитор'
  if (id.startsWith('fam_')) return 'Семья'
  if (id.startsWith('soc_')) return 'Общество'
  return 'Агент'
}

function roleClass(id: string): string {
  if (id.startsWith('off_')) return 'danger'
  if (id.startsWith('biz_')) return 'info'
  if (id.startsWith('aud_')) return 'accent'
  if (id.startsWith('fam_')) return 'warning'
  if (id.startsWith('soc_')) return 'success'
  return ''
}

function connectionCount(id: string, edges: GraphEdge[]): number {
  return edges.filter((e) => e.source === id || e.target === id).length
}

export function AgentList({ nodes, edges, events, selectedNode, names, onSelect }: Props) {
  const [expandedRep, setExpandedRep] = useState<string | null>(null)

  if (nodes.length === 0) {
    return (
      <div className="agent-list-empty">
        <span className="text-muted">Агенты появятся при запуске прогона</span>
      </div>
    )
  }

  const sorted = [...nodes].sort((a, b) => b.reputation - a.reputation)

  return (
    <div className="agent-list">
      <div className="agent-list-header">Агенты ({nodes.length})</div>
      {sorted.map((node) => {
        const name = names[node.id] ?? node.id
        const maxRep = 10
        const repPct = Math.max(0, Math.min(100, (node.reputation / maxRep) * 100))
        const suspicious = edges.some(
          (e) => (e.source === node.id || e.target === node.id) && e.strength >= SUSPICIOUS_THRESHOLD
        )
        const isExpanded = expandedRep === node.id

        // История репутации для данного агента
        const repHistory = events
          .filter((e) => e.event_type === 'reputation_modified' && e.payload.target === node.id)
          .slice(-5)

        return (
          <div key={node.id}>
            <div
              className={`agent-list-item${selectedNode === node.id ? ' selected' : ''}${suspicious ? ' suspicious' : ''}`}
              onClick={() => onSelect(node.id === selectedNode ? '' : node.id)}
            >
              <div className="agent-list-row">
                <span className={`agent-dot ${roleClass(node.id)}`} />
                <span className="agent-list-name">{name}</span>
                <span className={`badge ${roleClass(node.id)} small`}>{roleLabel(node.id)}</span>
              </div>
              <div className="agent-list-row" style={{ gap: '0.5rem', marginTop: '3px' }}>
                <div className="rep-bar">
                  <div
                    className={`rep-bar-fill ${node.reputation < 5 ? 'danger' : node.reputation < 7 ? 'warn' : ''}`}
                    style={{ width: `${repPct}%` }}
                  />
                </div>
                <span className="agent-list-rep">{node.reputation.toFixed(1)}</span>
                <span className="text-muted" style={{ fontSize: '0.65rem' }}>
                  {connectionCount(node.id, edges)} св.
                </span>
                {repHistory.length > 0 && (
                  <button
                    className="rep-history-toggle"
                    onClick={(e) => {
                      e.stopPropagation()
                      setExpandedRep(isExpanded ? null : node.id)
                    }}
                    title="История репутации"
                  >
                    {isExpanded ? '▲' : '▼'}
                  </button>
                )}
              </div>
            </div>

            {isExpanded && repHistory.length > 0 && (
              <div className="rep-history">
                {repHistory.map((ev, i) => {
                  const delta = typeof ev.payload.delta === 'number' ? ev.payload.delta : 0
                  const reason = typeof ev.payload.reason === 'string' ? ev.payload.reason : ''
                  const sign = delta >= 0 ? '+' : ''
                  return (
                    <div key={i} className="rep-history-item">
                      <span className="rep-history-round">R{ev.round}</span>
                      <span className={`rep-history-delta ${delta >= 0 ? 'success' : 'danger'}`}>
                        {sign}{delta.toFixed(2)}
                      </span>
                      {reason && <span className="rep-history-reason">{reason}</span>}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
