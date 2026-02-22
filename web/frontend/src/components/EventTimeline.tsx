import { useEffect, useRef, useState } from 'react'
import type { SimEvent } from '../types'

const EVENT_LABELS: Record<string, string> = {
  message_sent: 'MSG',
  graph_updated: 'GRAPH',
  reputation_modified: 'REP',
  case_opened: 'CASE+',
  case_resolved: 'CASE✓',
  case_modified: 'CASE~',
  proposal_submitted: 'PROP',
  evidence_added: 'EVID+',
  evidence_removed: 'EVID-',
  arbiter_approved: 'ARB+',
  arbiter_rejected: 'ARB-',
  auto_transition: 'AUTO',
  world_event: 'WORLD',
}

const EVENT_BADGE_CLASS: Record<string, string> = {
  message_sent: 'badge',
  reputation_modified: 'badge warning',
  case_opened: 'badge accent',
  case_resolved: 'badge success',
  arbiter_approved: 'badge success',
  arbiter_rejected: 'badge danger',
  world_event: 'badge info',
}

interface Props {
  events: SimEvent[]
  selectedAgent?: string | null
}

export function EventTimeline({ events, selectedAgent }: Props) {
  const trackRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const [visibleIds, setVisibleIds] = useState<Set<number>>(new Set())

  const filtered = selectedAgent
    ? events.filter(
        (e) =>
          e.agent_id === selectedAgent ||
          e.payload.to_id === selectedAgent ||
          e.payload.target === selectedAgent
      )
    : events

  useEffect(() => {
    const timer = setTimeout(() => {
      setVisibleIds(new Set(filtered.map((_, i) => i)))
    }, 50)
    return () => clearTimeout(timer)
  }, [filtered.length])

  useEffect(() => {
    if (!autoScroll || !trackRef.current) return
    trackRef.current.scrollLeft = trackRef.current.scrollWidth
  }, [events.length, autoScroll])

  return (
    <div className="event-timeline">
      <div className="event-timeline-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span className="event-timeline-title">
            {selectedAgent ? `Фильтр: ${selectedAgent}` : 'Лента событий'}
          </span>
          {selectedAgent && (
            <span className="badge accent">{selectedAgent}</span>
          )}
        </div>
        <span className="event-timeline-count">{filtered.length} событий</span>
      </div>

      <div
        ref={trackRef}
        className="event-timeline-track"
        onMouseEnter={() => setAutoScroll(false)}
        onMouseLeave={() => setAutoScroll(true)}
      >
        {filtered.map((e, i) => (
          <div
            key={i}
            className={`event-card ${visibleIds.has(i) ? 'visible' : ''}`}
          >
            <div className="corner tl" />
            <div className="corner br" />
            <div className="event-card-round">R{e.round}</div>
            <div>
              <span className={EVENT_BADGE_CLASS[e.event_type] ?? 'badge'}>
                {EVENT_LABELS[e.event_type] ?? e.event_type.slice(0, 6)}
              </span>
            </div>
            <div className="event-card-agent">{e.agent_id || '—'}</div>
            {Boolean(e.payload.private) && (
              <span className="badge violet" style={{ fontSize: '0.45rem' }}>PRIV</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
