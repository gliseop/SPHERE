import { useState } from 'react'
import type { GraphEdge, GraphNode, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'
import { getNumber } from '../utils/payload'

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
  if (id === 'auditor' || id.startsWith('aud_')) return 'Аудитор'
  if (id.startsWith('juror_')) return 'Присяжный'
  if (id.startsWith('fam_')) return 'Семья'
  if (id.startsWith('soc_')) return 'Общество'
  return 'Агент'
}

function roleClass(id: string): string {
  if (id.startsWith('off_')) return 'danger'
  if (id.startsWith('biz_')) return 'info'
  if (id === 'auditor' || id.startsWith('aud_')) return 'accent'
  if (id.startsWith('juror_')) return 'violet'
  if (id.startsWith('fam_')) return 'warning'
  if (id.startsWith('soc_')) return 'success'
  return ''
}

function connectionCount(id: string, edges: GraphEdge[]): number {
  return edges.filter((e) => e.source === id || e.target === id).length
}

function repTooltip(node: GraphNode): string {
  if (node.has_reputation === false) return 'Репутация не применяется к этому агенту'
  const frozen = node.reputation_frozen ? 'заморожена' : 'активна'
  const title = node.position_title ? `\nДолжность: ${node.position_title}` : ''
  const next = (typeof node.next_position_threshold === 'number' && node.next_position_title)
    ? `\nСледующая: ${node.next_position_title} (≥ ${node.next_position_threshold})`
    : ''
  return `Репутация: ${node.reputation.toFixed(1)} (${frozen})${title}${next}`
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

  const sorted = [...nodes].sort((a, b) => {
    const ar = a.has_reputation === false ? Number.NEGATIVE_INFINITY : a.reputation
    const br = b.has_reputation === false ? Number.NEGATIVE_INFINITY : b.reputation
    return br - ar
  })

  return (
    <div className="agent-list">
      <div className="agent-list-header">Агенты ({nodes.length})</div>
      {sorted.map((node) => {
        const name = names[node.id] ?? node.id
        const hasRep = node.has_reputation !== false
        const target = typeof node.next_position_threshold === 'number'
          ? node.next_position_threshold
          : null
        const barMax = hasRep
          ? (target && target > 0 ? target : Math.max(node.reputation, 50))
          : 1
        const repPct = hasRep
          ? Math.max(0, Math.min(100, (node.reputation / barMax) * 100))
          : 0
        const suspicious = edges.some(
          (e) => (e.source === node.id || e.target === node.id) && e.strength >= SUSPICIOUS_THRESHOLD
        )
        const isExpanded = expandedRep === node.id

        const repHistory = events
          .filter((e) => e.event_type === 'reputation_snapshot' && e.agent_id === node.id)
          .slice(-40)

        const totalDelta = repHistory.reduce(
          (sum, ev) => sum + (getNumber(ev.payload, 'delta') ?? 0),
          0,
        )

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
                <div className="rep-bar" title={repTooltip(node)}>
                  {hasRep ? (
                    <div
                      className={`rep-bar-fill ${node.reputation_frozen ? 'danger' : ''}`}
                      style={{ width: `${repPct}%` }}
                    />
                  ) : (
                    <div className="rep-bar-fill muted" style={{ width: '100%' }} />
                  )}
                </div>
                <span className="agent-list-rep">
                  {hasRep ? node.reputation.toFixed(1) : '—'}
                </span>
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
                <div className="rep-history-summary">
                  {repHistory.length} изм., {'\u0394'} total:{' '}
                  <span className={totalDelta >= 0 ? 'rep-history-delta success' : 'rep-history-delta danger'}>
                    {totalDelta >= 0 ? '+' : ''}{totalDelta.toFixed(2)}
                  </span>
                </div>
                <div className="rep-history-scroll">
                  {repHistory.map((ev, i) => {
                    const delta = getNumber(ev.payload, 'delta') ?? 0
                    const cases = getNumber(ev.payload, 'cases_resolved')
                    const complaints = getNumber(ev.payload, 'complaints_received')
                    const sign = delta >= 0 ? '+' : ''
                    return (
                      <div key={i} className="rep-history-item">
                        <span className="rep-history-round">
                          {typeof ev.round === 'number'
                            ? `R${ev.round}`
                            : (() => {
                                const ms = ev.timestamp ? Date.parse(ev.timestamp) : NaN
                                if (!Number.isFinite(ms)) return '--:--'
                                const d = new Date(ms)
                                return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
                              })()}
                        </span>
                        <span className={`rep-history-delta ${delta >= 0 ? 'success' : 'danger'}`}>
                          {sign}{delta.toFixed(2)}
                        </span>
                        {(cases !== null || complaints !== null) && (
                          <span className="rep-history-reason">
                            {cases !== null ? `дел: ${cases}` : ''}
                            {complaints !== null ? `  жалоб: ${complaints}` : ''}
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
