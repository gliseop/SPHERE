import type { GraphEdge, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD, agentRoleBadgeClass, agentRoleLabel } from '../constants'

interface D3Node {
  id: string
  reputation: number
  has_reputation?: boolean
  reputation_frozen?: boolean
  position_title?: string
  next_position_title?: string
  next_position_threshold?: number
  x?: number
  y?: number
}

interface Props {
  node: D3Node
  edges: GraphEdge[]
  events: SimEvent[]
  names: Record<string, string>
  x: number
  y: number
}

function displayName(id: string, names: Record<string, string>): string {
  return names[id] ?? id
}

export function NodeTooltip({ node, edges, events, names, x, y }: Props) {
  const connections = edges.filter(
    (e) => e.source === node.id || e.target === node.id
  )
  const suspiciousCount = connections.filter(
    (e) => e.strength >= SUSPICIOUS_THRESHOLD
  ).length

  // Собрать историю репутации для этого агента (последние 5)
  const repHistory = events
    .filter((e) => e.event_type === 'reputation_snapshot' && e.agent_id === node.id)
    .slice(-5)
  const hasRep = node.has_reputation !== false

  const style: React.CSSProperties = {
    left: x + 14,
    top: y - 10,
  }

  if (x > window.innerWidth - 260) {
    style.left = x - 200
  }

  return (
    <div className="node-tooltip hud-panel" style={style}>
      <div className="corner tl accent" />
      <div className="corner tr accent" />
      <div className="corner bl" />
      <div className="corner br" />

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
        <span style={{ fontSize: '0.625rem', fontWeight: 700, letterSpacing: '0.05em' }}>
          {displayName(node.id, names)}
        </span>
        <span className={agentRoleBadgeClass(node.id)}>{agentRoleLabel(node.id)}</span>
      </div>

      <div className="hud-divider" />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <div className="stat-card" style={{ padding: '0.375rem 0.5rem' }}>
          <div className="stat-label">Репутация</div>
          <div className="stat-value" style={{ fontSize: '1rem' }}>
            {hasRep ? node.reputation.toFixed(1) : '—'}
          </div>
        </div>
        <div className="stat-card" style={{ padding: '0.375rem 0.5rem' }}>
          <div className="stat-label">Связи</div>
          <div className={`stat-value ${suspiciousCount > 0 ? 'danger' : ''}`} style={{ fontSize: '1rem' }}>
            {connections.length}
            {suspiciousCount > 0 && (
              <span className="stat-unit" style={{ color: '#ef4444', fontSize: '0.5rem' }}>
                {' '}⚠{suspiciousCount}
              </span>
            )}
          </div>
        </div>
      </div>

      {connections.length > 0 && (
        <div style={{ fontSize: '0.5rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
          {connections
            .sort((a, b) => b.strength - a.strength)
            .slice(0, 3)
            .map((c, i) => {
              const other = c.source === node.id ? c.target : c.source
              const sus = c.strength >= SUSPICIOUS_THRESHOLD
              return (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '0.15rem 0', borderBottom: '1px solid var(--border)' }}>
                  <span style={{ color: 'var(--text-primary)' }}>{displayName(String(other), names)}</span>
                  <span style={{ color: sus ? '#ef4444' : 'var(--text-tertiary)' }}>
                    {c.strength.toFixed(1)}
                  </span>
                </div>
              )
            })}
        </div>
      )}

      {/* История репутации */}
      {repHistory.length > 0 && (
        <>
          <div className="hud-divider" />
          <div style={{ fontSize: '0.5rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: '0.25rem' }}>
            История репутации
          </div>
          {repHistory.map((e, i) => {
            const delta = typeof e.payload.delta === 'number' ? e.payload.delta : 0
            const cases = typeof e.payload.cases_resolved === 'number' ? e.payload.cases_resolved : null
            const complaints = typeof e.payload.complaints_received === 'number' ? e.payload.complaints_received : null
            const sign = delta >= 0 ? '+' : ''
            return (
              <div key={i} style={{ display: 'flex', gap: '0.35rem', alignItems: 'baseline', padding: '0.1rem 0', borderBottom: '1px solid var(--border)', fontSize: '0.5rem' }}>
                <span style={{ color: 'var(--accent)', flexShrink: 0 }}>R{e.round}</span>
                <span style={{ color: delta >= 0 ? 'var(--success)' : 'var(--danger)', fontWeight: 600, flexShrink: 0 }}>
                  {sign}{delta.toFixed(2)}
                </span>
                {(cases !== null || complaints !== null) && (
                  <span style={{ color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {cases !== null ? `дел: ${cases}` : ''}
                    {complaints !== null ? `  жалоб: ${complaints}` : ''}
                  </span>
                )}
              </div>
            )
          })}
        </>
      )}
    </div>
  )
}
