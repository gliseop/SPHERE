import type { GraphEdge, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'

interface D3Node {
  id: string
  reputation: number
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

function roleLabel(id: string): string {
  if (id.startsWith('off_')) return 'Чиновник'
  if (id.startsWith('biz_')) return 'Подрядчик'
  if (id.startsWith('aud_')) return 'Аудитор'
  if (id.startsWith('fam_')) return 'Семья'
  if (id.startsWith('soc_')) return 'Общество'
  return 'Агент'
}

function roleBadgeClass(id: string): string {
  if (id.startsWith('off_')) return 'badge danger'
  if (id.startsWith('biz_')) return 'badge info'
  if (id.startsWith('aud_')) return 'badge accent'
  if (id.startsWith('fam_')) return 'badge warning'
  if (id.startsWith('soc_')) return 'badge success'
  return 'badge'
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
    .filter((e) => e.event_type === 'reputation_modified' && e.payload.target === node.id)
    .slice(-5)

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
        <span className={roleBadgeClass(node.id)}>{roleLabel(node.id)}</span>
      </div>

      <div className="hud-divider" />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <div className="stat-card" style={{ padding: '0.375rem 0.5rem' }}>
          <div className="stat-label">Репутация</div>
          <div className="stat-value" style={{ fontSize: '1rem' }}>
            {node.reputation.toFixed(1)}
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
            const reason = typeof e.payload.reason === 'string' ? e.payload.reason : ''
            const sign = delta >= 0 ? '+' : ''
            return (
              <div key={i} style={{ display: 'flex', gap: '0.35rem', alignItems: 'baseline', padding: '0.1rem 0', borderBottom: '1px solid var(--border)', fontSize: '0.5rem' }}>
                <span style={{ color: 'var(--accent)', flexShrink: 0 }}>R{e.round}</span>
                <span style={{ color: delta >= 0 ? 'var(--success)' : 'var(--danger)', fontWeight: 600, flexShrink: 0 }}>
                  {sign}{delta.toFixed(2)}
                </span>
                {reason && (
                  <span style={{ color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {reason}
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
