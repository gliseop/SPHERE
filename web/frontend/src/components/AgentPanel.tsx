import type { GraphEdge, GraphNode, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'
import { getString, getBool } from '../utils/payload'

interface Props {
  nodeId: string | null
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
}

function roleLabel(id: string): string {
  if (id.startsWith('off_')) return 'Чиновник'
  if (id.startsWith('biz_')) return 'Подрядчик'
  if (id.startsWith('aud_')) return 'Аудитор'
  return 'Агент'
}

function roleBadgeClass(id: string): string {
  if (id.startsWith('off_')) return 'badge danger'
  if (id.startsWith('biz_')) return 'badge info'
  if (id.startsWith('aud_')) return 'badge accent'
  return 'badge'
}

export function AgentPanel({ nodeId, nodes, edges, events }: Props) {
  if (!nodeId) {
    return (
      <div className="agent-panel-empty">
        <div style={{ fontSize: '1.5rem', opacity: 0.3 }}>◈</div>
        <div>Выберите агента</div>
        <div style={{ opacity: 0.6 }}>Кликните на узел графа</div>
      </div>
    )
  }

  const node = nodes.find((n) => n.id === nodeId)
  const connections = edges
    .filter((e) => e.source === nodeId || e.target === nodeId)
    .sort((a, b) => b.strength - a.strength)
  const messages: SimEvent[] = []
  for (let i = events.length - 1; i >= 0 && messages.length < 20; i--) {
    const e = events[i]
    if (e.event_type !== 'message_sent' && e.event_type !== 'message') continue
    if (e.agent_id !== nodeId && getString(e.payload, 'to_id') !== nodeId) continue
    messages.push(e)
  }
  messages.reverse()

  const suspiciousCount = connections.filter((c) => c.strength >= SUSPICIOUS_THRESHOLD).length
  const reputation = node?.reputation ?? 0
  const reputationClass = reputation < 5 ? 'danger' : reputation < 8 ? '' : 'success'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div className="hud-panel compact" style={{ borderBottom: '1px solid var(--border)', borderLeft: 'none', borderRight: 'none', borderTop: 'none' }}>
        <div className="corner tl accent" />
        <div className="corner tr" />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
          <span style={{ fontSize: '0.75rem', fontWeight: 700, letterSpacing: '0.05em' }}>
            {nodeId}
          </span>
          <span className={roleBadgeClass(nodeId)}>{roleLabel(nodeId)}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
          <div className="stat-card" style={{ padding: '0.4rem 0.5rem' }}>
            <div className="stat-label">Репутация</div>
            <div className={`stat-value ${reputationClass}`} style={{ fontSize: '1.125rem' }}>
              {reputation.toFixed(1)}
            </div>
          </div>
          <div className="stat-card" style={{ padding: '0.4rem 0.5rem' }}>
            <div className="stat-label">Связи</div>
            <div className={`stat-value ${suspiciousCount > 0 ? 'danger' : ''}`} style={{ fontSize: '1.125rem' }}>
              {connections.length}
              {suspiciousCount > 0 && (
                <span className="stat-unit" style={{ color: '#ef4444', fontSize: '0.5rem' }}>
                  {' '}⚠{suspiciousCount}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div style={{ flex: '0 0 auto', borderBottom: '1px solid var(--border)', padding: '0 0.875rem' }}>
        <div className="section-label" style={{ padding: '0.5rem 0 0.375rem' }}>
          Связи ({connections.length})
        </div>
        <div style={{ maxHeight: '100px', overflowY: 'auto' }}>
          {connections.map((c, i) => {
            const other = c.source === nodeId ? c.target : c.source
            const sus = c.strength >= SUSPICIOUS_THRESHOLD
            return (
              <div key={i} className="conn-item">
                <span className="conn-item-name">{String(other)}</span>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                  <span className={`conn-item-strength ${sus ? 'suspicious' : ''}`}>
                    {c.strength.toFixed(1)}
                  </span>
                  {sus && <span className="badge danger" style={{ fontSize: '0.45rem' }}>⚠</span>}
                </div>
              </div>
            )
          })}
          {connections.length === 0 && (
            <div style={{ fontSize: '0.6rem', color: 'var(--text-tertiary)', padding: '0.25rem 0' }}>
              Нет связей
            </div>
          )}
        </div>
      </div>

      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <div className="section-label">Сообщения ({messages.length})</div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 0.875rem 0.5rem' }}>
          {messages.map((e, i) => {
            const isFrom = e.agent_id === nodeId
            const other = isFrom ? getString(e.payload, 'to_id') : e.agent_id
            return (
              <div key={i} className={`msg-item ${getBool(e.payload, 'private') ? 'msg-item-private' : ''}`}>
                <span className="msg-item-round">
                  {typeof e.round === 'number'
                    ? `R${e.round}`
                    : (() => {
                        const ms = e.timestamp ? Date.parse(e.timestamp) : NaN
                        if (!Number.isFinite(ms)) return '--:--'
                        const d = new Date(ms)
                        return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
                      })()}
                </span>
                <span className="msg-item-dir">{isFrom ? '→' : '←'}</span>
                <span className="msg-item-agent">{String(other)}</span>
                {getBool(e.payload, 'private') && (
                  <span className="badge violet" style={{ fontSize: '0.45rem', marginLeft: 'auto' }}>PRIV</span>
                )}
              </div>
            )
          })}
          {messages.length === 0 && (
            <div style={{ fontSize: '0.6rem', color: 'var(--text-tertiary)', paddingTop: '0.25rem' }}>
              Нет сообщений
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
