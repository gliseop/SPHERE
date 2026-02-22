import type { GraphEdge, GraphNode, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'

interface Props {
  nodeId: string | null
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
}

export function AgentPanel({ nodeId, nodes, edges, events }: Props) {
  if (!nodeId) {
    return (
      <div className="p-3 text-slate-500 text-sm">
        Кликните на агента в графе
      </div>
    )
  }

  const node = nodes.find((n) => n.id === nodeId)
  const connections = edges.filter(
    (e) => e.source === nodeId || e.target === nodeId
  )
  const messages = events.filter(
    (e) =>
      e.event_type === 'message_sent' &&
      (e.agent_id === nodeId || e.payload.to_id === nodeId)
  )

  function roleLabel(id: string): string {
    if (id.startsWith('off_')) return 'Чиновник'
    if (id.startsWith('biz_')) return 'Подрядчик'
    if (id.startsWith('aud_')) return 'Аудитор'
    return 'Агент'
  }

  return (
    <div className="p-2 space-y-3 text-sm overflow-y-auto h-full">
      <div>
        <div className="font-mono text-base text-white">{nodeId}</div>
        <div className="text-slate-400">{roleLabel(nodeId)}</div>
        <div className="text-slate-300 mt-1">
          Репутация: <span className="font-bold">{node?.reputation.toFixed(1) ?? '—'}</span>
        </div>
      </div>

      <div>
        <div className="text-slate-400 text-xs mb-1">Связи ({connections.length})</div>
        <div className="space-y-0.5">
          {connections.map((c, i) => {
            const other = c.source === nodeId ? c.target : c.source
            const suspicious = c.strength >= SUSPICIOUS_THRESHOLD
            return (
              <div key={i} className="flex justify-between text-xs">
                <span className="font-mono text-slate-300">{String(other)}</span>
                <span className={suspicious ? 'text-red-400' : 'text-slate-500'}>
                  {c.strength.toFixed(1)}
                  {suspicious ? ' ⚠️' : ''}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      <div>
        <div className="text-slate-400 text-xs mb-1">Сообщения ({messages.length})</div>
        <div className="space-y-1 max-h-48 overflow-y-auto">
          {messages.slice(-10).map((e, i) => {
            const isFrom = e.agent_id === nodeId
            const other = isFrom ? e.payload.to_id : e.agent_id
            return (
              <div key={i} className="text-xs text-slate-400">
                <span className={e.payload.private ? 'text-violet-400' : 'text-slate-300'}>
                  R{e.round} {isFrom ? '→' : '←'} {String(other)}
                  {e.payload.private ? ' 🔒' : ''}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
