import { useEffect, useRef } from 'react'
import type { SimEvent } from '../types'

interface Props {
  events: SimEvent[]
  names: Record<string, string>
  selectedAgent: string | null
}

function displayName(id: string, names: Record<string, string>): string {
  return names[id] ?? id
}

function eventBadgeClass(eventType: string): string {
  switch (eventType) {
    case 'message_sent': return 'info'
    case 'self_reflection': return 'accent'
    case 'world_event': return 'success'
    default: return ''
  }
}

function eventBadgeLabel(eventType: string): string {
  switch (eventType) {
    case 'message_sent': return 'MSG'
    case 'self_reflection': return 'РЕФЛ'
    case 'world_event': return 'МИР'
    case 'reputation_modified': return 'РЕП'
    case 'graph_updated': return 'ГРАФ'
    default: return eventType.slice(0, 4).toUpperCase()
  }
}

export function ActivityFeed({ events, names, selectedAgent }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const pausedRef = useRef(false)

  useEffect(() => {
    if (!pausedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [events.length])

  const filtered = selectedAgent
    ? events.filter((e) => {
        if (e.agent_id === selectedAgent) return true
        if (e.payload.to_id === selectedAgent) return true
        if (e.payload.target === selectedAgent) return true
        return false
      })
    : events

  // Group by round
  const grouped: { round: number; items: SimEvent[] }[] = []
  for (const e of filtered) {
    const last = grouped[grouped.length - 1]
    if (!last || last.round !== e.round) {
      grouped.push({ round: e.round, items: [e] })
    } else {
      last.items.push(e)
    }
  }

  return (
    <div className="activity-feed">
      <div className="activity-feed-header">
        Активность{selectedAgent ? ` — ${displayName(selectedAgent, names)}` : ''}
      </div>
      <div
        className="activity-feed-scroll"
        onMouseEnter={() => { pausedRef.current = true }}
        onMouseLeave={() => { pausedRef.current = false }}
      >
        {grouped.length === 0 && (
          <div className="activity-feed-empty">
            <span className="text-muted">Запустите прогон чтобы увидеть активность</span>
          </div>
        )}
        {grouped.map(({ round, items }) => (
          <div key={round} className="activity-round-group">
            <div className="activity-round-label">Раунд {round}</div>
            {items.map((e, i) => (
              <ActivityItem key={i} event={e} names={names} />
            ))}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

function ActivityItem({ event, names }: { event: SimEvent; names: Record<string, string> }) {
  const { event_type, agent_id, payload } = event

  if (event_type === 'world_event' && payload.narrative) {
    return (
      <div className="activity-item world">
        <span className="activity-icon">🌍</span>
        <span className="activity-narrative">{payload.narrative as string}</span>
      </div>
    )
  }

  if (event_type === 'self_reflection') {
    return (
      <div className="activity-item reflection">
        <div className="activity-item-header">
          <span className="activity-icon">💭</span>
          <span className="activity-agent">{displayName(agent_id, names)}</span>
          <span className="badge accent small">РЕФЛ</span>
        </div>
        <div className="activity-content italic">{payload.content as string}</div>
      </div>
    )
  }

  if (event_type === 'message_sent') {
    const toId = payload.to_id as string
    const isPrivate = Boolean(payload.private)
    const content = payload.content as string
    return (
      <div className="activity-item message">
        <div className="activity-item-header">
          <span className="activity-agent">{displayName(agent_id, names)}</span>
          <span className="activity-arrow">{isPrivate ? '⇢' : '→'}</span>
          <span className="activity-agent">{displayName(toId, names)}</span>
          {isPrivate && <span className="badge accent small">ПРИВ</span>}
        </div>
        {content && content !== 'msg' && (
          <div className="activity-content">{content}</div>
        )}
      </div>
    )
  }

  if (event_type === 'reputation_modified') {
    const target = payload.target as string
    const delta = payload.delta as number
    const sign = delta >= 0 ? '+' : ''
    return (
      <div className="activity-item reputation">
        <span className="activity-agent">{displayName(target, names)}</span>
        <span className={`activity-delta ${delta >= 0 ? 'success' : 'danger'}`}>
          {sign}{delta.toFixed(2)}
        </span>
        <span className="badge small">РЕП</span>
      </div>
    )
  }

  // Other events — compact
  return (
    <div className="activity-item generic">
      <span className={`badge ${eventBadgeClass(event_type)} small`}>
        {eventBadgeLabel(event_type)}
      </span>
      <span className="activity-agent text-muted">{displayName(agent_id, names)}</span>
    </div>
  )
}
