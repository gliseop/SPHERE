import type { GraphEdge, GraphNode } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  selectedNode: string | null
  names: Record<string, string>
  onSelect: (id: string) => void
}

function roleLabel(id: string): string {
  if (id.startsWith('off_')) return 'Чиновник'
  if (id.startsWith('biz_')) return 'Подрядчик'
  if (id.startsWith('aud_')) return 'Аудитор'
  return 'Агент'
}

function roleClass(id: string): string {
  if (id.startsWith('off_')) return 'danger'
  if (id.startsWith('biz_')) return 'info'
  if (id.startsWith('aud_')) return 'accent'
  return ''
}

function connectionCount(id: string, edges: GraphEdge[]): number {
  return edges.filter((e) => e.source === id || e.target === id).length
}

export function AgentList({ nodes, edges, selectedNode, names, onSelect }: Props) {
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

        return (
          <div
            key={node.id}
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
            </div>
          </div>
        )
      })}
    </div>
  )
}
