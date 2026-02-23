import { useEffect, useRef } from 'react'
import type { SimEvent } from '../types'

interface Props {
  events: SimEvent[]
  names: Record<string, string>
  selectedAgent: string | null
  onClearFilter?: () => void
  focusRound?: number | null
}

function displayName(id: string | undefined, names: Record<string, string>): string {
  if (!id) return ''
  return names[id] ?? id
}

function isAuditor(id: string): boolean {
  return id.startsWith('aud_')
}

export function ActivityFeed({ events, names, selectedAgent, onClearFilter, focusRound }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pausedRef = useRef(false)

  useEffect(() => {
    if (!pausedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [events.length, selectedAgent])

  // Прокрутка к раунду при focusRound
  useEffect(() => {
    if (focusRound == null || !scrollRef.current) return
    const el = scrollRef.current.querySelector(`[data-round="${focusRound}"]`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [focusRound])

  const filtered = selectedAgent
    ? events.filter((e) => {
        if (e.agent_id === selectedAgent) return true
        if (e.payload.to_id === selectedAgent) return true
        if (e.payload.target === selectedAgent) return true
        if (e.event_type === 'graph_updated') {
          if (e.payload.agent_a === selectedAgent || e.payload.agent_b === selectedAgent) return true
        }
        return false
      })
    : events

  // Group by round
  const sorted = [...filtered].sort((a, b) => a.round - b.round)
  const grouped: { round: number; items: SimEvent[] }[] = []
  for (const e of sorted) {
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
        <span>
          Активность{selectedAgent ? ` — ${displayName(selectedAgent, names)}` : ''}
        </span>
        {selectedAgent && onClearFilter && (
          <button
            className="btn-clipped small"
            onClick={onClearFilter}
            style={{ marginLeft: '0.5rem', padding: '0.15rem 0.5rem', fontSize: '0.6rem' }}
          >
            ✕ Сбросить
          </button>
        )}
      </div>
      <div
        ref={scrollRef}
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
          <div key={round} className="activity-round-group" data-round={round}>
            <div className={`activity-round-label${focusRound === round ? ' focused' : ''}`}>
              Раунд {round}
            </div>
            {items.map((e) => (
              <ActivityItem
                key={`${e.round}-${e.event_type}-${e.agent_id}-${e.timestamp}`}
                event={e}
                names={names}
              />
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
  const auditor = isAuditor(agent_id)

  if (event_type === 'world_event' && payload.narrative) {
    return (
      <div className="activity-item world">
        <span className="activity-icon">🌍</span>
        <span className="activity-narrative">{payload.narrative as string}</span>
      </div>
    )
  }

  if (event_type === 'self_reflection') {
    const content = typeof payload.content === 'string' ? payload.content : ''
    return (
      <div className={`activity-item reflection${auditor ? ' auditor' : ''}`}>
        <div className="activity-item-header">
          <span className="activity-icon">{auditor ? '🔍' : '💭'}</span>
          <span className="activity-agent">{displayName(agent_id, names)}</span>
          {auditor && <span className="badge accent small">ИИ-АУДИТОР</span>}
          <span className="badge accent small">РЕФЛ</span>
        </div>
        {content && <div className="activity-content italic">{content}</div>}
      </div>
    )
  }

  if (event_type === 'message_sent') {
    const toId = typeof payload.to_id === 'string' ? payload.to_id : ''
    const isPrivate = Boolean(payload.private)
    const content = typeof payload.content === 'string' ? payload.content : ''
    const displayContent = content && content !== 'msg' ? content : '[сообщение]'
    return (
      <div className={`activity-item message${auditor ? ' auditor' : ''}`}>
        <div className="activity-item-header">
          {auditor && <span className="activity-icon">🔍</span>}
          <span className="activity-agent">{displayName(agent_id, names)}</span>
          <span className="activity-arrow">{isPrivate ? '⇢' : '→'}</span>
          <span className="activity-agent">{displayName(toId, names)}</span>
          {isPrivate && <span className="badge accent small">ПРИВ</span>}
          {auditor && <span className="badge accent small">ИИ-АУДИТОР</span>}
        </div>
        <div className="activity-content">{displayContent}</div>
      </div>
    )
  }

  if (event_type === 'graph_updated') {
    const agentA = typeof payload.agent_a === 'string' ? payload.agent_a : ''
    const agentB = typeof payload.agent_b === 'string' ? payload.agent_b : ''
    const delta = typeof payload.delta === 'number' ? payload.delta : 0
    const sign = delta >= 0 ? '+' : ''
    return (
      <div className="activity-item graph-link">
        <span className="activity-icon">🔗</span>
        <span className="activity-agent">{displayName(agentA, names)}</span>
        <span className="activity-arrow">↔</span>
        <span className="activity-agent">{displayName(agentB, names)}</span>
        <span className={`activity-delta ${delta >= 0 ? 'success' : 'danger'}`}>
          {sign}{delta.toFixed(2)}
        </span>
      </div>
    )
  }

  if (event_type === 'reputation_modified') {
    const target = typeof payload.target === 'string' ? payload.target : ''
    const delta = typeof payload.delta === 'number' ? payload.delta : 0
    const reason = typeof payload.reason === 'string' ? payload.reason : ''
    const sign = delta >= 0 ? '+' : ''
    return (
      <div className="activity-item reputation">
        <div className="activity-item-header">
          <span className={`activity-rep-arrow ${delta >= 0 ? 'success' : 'danger'}`}>
            {delta >= 0 ? '▲' : '▼'}
          </span>
          <span className="activity-agent">{displayName(target, names)}</span>
          <span className={`activity-delta ${delta >= 0 ? 'success' : 'danger'}`}>
            {sign}{delta.toFixed(2)}
          </span>
          <span className="badge small">РЕП</span>
        </div>
        {reason && <div className="activity-content text-muted">{reason}</div>}
      </div>
    )
  }

  // Other events — compact
  return (
    <div className="activity-item generic">
      <span className="badge small">
        {event_type.replace(/_/g, ' ').toUpperCase().slice(0, 12)}
      </span>
      <span className="activity-agent text-muted">{displayName(agent_id, names)}</span>
    </div>
  )
}
