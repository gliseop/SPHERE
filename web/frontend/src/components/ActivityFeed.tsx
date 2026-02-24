import { useEffect, useMemo, useRef, useState } from 'react'
import type { SimEvent, ThreadGroup } from '../types'
import { getString, getBool } from '../utils/payload'
import { toDayKey, formatDayLabel } from '../utils/time'

interface Props {
  events: SimEvent[]
  names: Record<string, string>
  selectedAgent: string | null
  onClearFilter?: () => void
  focusDay?: string | null
}

interface DayGroup {
  dayKey: string
  label: string
  items: DayItem[]
}

type DayItem =
  | { kind: 'hour'; label: string }
  | { kind: 'thread'; thread: ThreadGroup }
  | { kind: 'event'; event: SimEvent }

function dn(id: string | undefined, names: Record<string, string>): string {
  if (!id) return ''
  return names[id] ?? id
}

function toMillis(ts: string): number {
  if (!ts) return Number.NaN
  const time = Date.parse(ts)
  return Number.isFinite(time) ? time : Number.NaN
}

function formatHourLabel(ts: string): string {
  const ms = toMillis(ts)
  if (Number.isNaN(ms)) return ''
  const d = new Date(ms)
  const hh = String(d.getHours()).padStart(2, '0')
  return `${hh}:00`
}

function fmtTime(ts: string): string {
  const ms = toMillis(ts)
  if (Number.isNaN(ms)) return ''
  const d = new Date(ms)
  return d.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function channelIcon(channel: string): string {
  if (channel === 'telegram') return '✈'
  if (channel === 'phone') return '☎'
  if (channel === 'email') return '✉'
  if (channel === 'face_to_face') return '🤝'
  if (channel === 'official_doc') return '📄'
  return '💬'
}

function eventTimestamp(event: SimEvent): string {
  return typeof event.timestamp === 'string' ? event.timestamp : ''
}

function belongsToAgent(event: SimEvent, agentId: string): boolean {
  if (event.agent_id === agentId) return true
  const payload = event.payload
  if (getString(payload, 'to_id') === agentId) return true
  if (getString(payload, 'target') === agentId) return true
  if (getString(payload, 'owner_id') === agentId) return true
  if (getString(payload, 'winner') === agentId) return true
  if (event.event_type === 'graph_updated') {
    if (getString(payload, 'agent_a') === agentId) return true
    if (getString(payload, 'agent_b') === agentId) return true
  }
  return false
}

function buildEntries(events: SimEvent[]): DayItem[] {
  // Pass 1: collect all messages by thread_id
  const threadMessages = new Map<string, SimEvent[]>()
  for (const event of events) {
    if (event.event_type === 'message') {
      const threadId = getString(event.payload, 'thread_id')
      if (threadId) {
        let msgs = threadMessages.get(threadId)
        if (!msgs) {
          msgs = []
          threadMessages.set(threadId, msgs)
        }
        msgs.push(event)
      }
    }
  }

  // Pass 2: iterate in order, rendering full thread at position of first message
  const renderedThreads = new Set<string>()
  const entries: DayItem[] = []

  for (const event of events) {
    const threadId = getString(event.payload, 'thread_id')

    if (event.event_type === 'message' && threadId) {
      // Skip if this thread was already rendered at an earlier position
      if (renderedThreads.has(threadId)) continue
      renderedThreads.add(threadId)

      const messages = threadMessages.get(threadId) ?? [event]
      const participantsSet = new Set<string>()
      for (const msg of messages) {
        participantsSet.add(msg.agent_id)
        const toId = getString(msg.payload, 'to_id')
        if (toId) participantsSet.add(toId)
      }
      const firstPayload = messages[0]?.payload ?? {}
      entries.push({
        kind: 'thread',
        thread: {
          thread_id: threadId,
          channel: getString(firstPayload, 'channel') || 'telegram',
          participants: [...participantsSet],
          messages,
          private: messages.some((msg) => getBool(msg.payload, 'private')),
        },
      })
      continue
    }

    entries.push({ kind: 'event', event })
  }
  return entries
}

function groupByDayAndHour(items: DayItem[]): DayGroup[] {
  const groups: DayGroup[] = []
  const lastHourByDay: Record<string, string> = {}

  for (const item of items) {
    const ts = item.kind === 'thread'
      ? eventTimestamp(item.thread.messages[0] ?? ({} as SimEvent))
      : item.kind === 'event'
        ? eventTimestamp(item.event)
        : ''

    const dayKey = toDayKey({ timestamp: ts })
    const hourLabel = formatHourLabel(ts)
    let group = groups[groups.length - 1]
    if (!group || group.dayKey !== dayKey) {
      group = {
        dayKey,
        label: formatDayLabel(dayKey),
        items: [],
      }
      groups.push(group)
      lastHourByDay[dayKey] = ''
    }

    if (hourLabel && lastHourByDay[dayKey] !== hourLabel) {
      group.items.push({ kind: 'hour', label: hourLabel })
      lastHourByDay[dayKey] = hourLabel
    }
    group.items.push(item)
  }

  return groups
}

function ThreadView({
  thread,
  names,
}: {
  thread: ThreadGroup
  names: Record<string, string>
}) {
  const firstSender = thread.messages[0]?.agent_id ?? ''
  const headerTime = fmtTime(eventTimestamp(thread.messages[0] ?? ({} as SimEvent)))
  return (
    <div className="thread-group">
      <div className="thread-header">
        <span className="channel-badge">{channelIcon(thread.channel)} {thread.channel}</span>
        <span className="thread-participants">
          {thread.participants.map((id) => dn(id, names)).join(' • ')}
        </span>
        {thread.private && <span className="private-badge">🔒 private</span>}
        {headerTime && <span className="activity-time">{headerTime}</span>}
      </div>
      <div className="thread-bubbles">
        {thread.messages.map((msg, idx) => {
          const mine = msg.agent_id === firstSender
          const bubbleTime = fmtTime(eventTimestamp(msg))
          const content = getString(msg.payload, 'content')
          return (
            <div
              key={`${thread.thread_id}-${idx}`}
              className={`thread-bubble ${mine ? 'left' : 'right'}`}
            >
              <div className="thread-bubble-author">{dn(msg.agent_id, names)}</div>
              <div className="thread-bubble-content">{content}</div>
              {bubbleTime && <div className="thread-bubble-time">{bubbleTime}</div>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function DocumentView({
  event,
  names,
  expanded,
  onToggle,
}: {
  event: SimEvent
  names: Record<string, string>
  expanded: boolean
  onToggle: () => void
}) {
  const payload = event.payload
  const docId = getString(payload, 'doc_id')
  const docType = getString(payload, 'doc_type')
  const title = getString(payload, 'title') || docId
  const content = getString(payload, 'content')
  const time = fmtTime(event.timestamp)
  return (
    <div className="document-card">
      <button className="document-header" onClick={onToggle}>
        <span className="document-title">{title || 'Документ'}</span>
        <span className="document-meta">
          <span className="badge small">{docType || 'document'}</span>
          <span className="badge small">{dn(event.agent_id, names)}</span>
          {time && <span className="activity-time">{time}</span>}
        </span>
        <span className="document-toggle">{expanded ? 'Свернуть' : 'Развернуть'}</span>
      </button>
      {expanded && <pre className="document-content">{content}</pre>}
    </div>
  )
}

function EventView({
  event,
  names,
  docExpanded,
  onToggleDoc,
}: {
  event: SimEvent
  names: Record<string, string>
  docExpanded: boolean
  onToggleDoc: (docId: string) => void
}) {
  const { event_type, agent_id, payload, timestamp } = event
  if (event_type === 'document_created') {
    const docId = getString(payload, 'doc_id')
    return (
      <DocumentView
        event={event}
        names={names}
        expanded={docExpanded}
        onToggle={() => onToggleDoc(docId)}
      />
    )
  }

  if (event_type === 'world_event') {
    return (
      <div className="activity-item world">
        <div className="activity-item-header">
          <span className="activity-icon">📢</span>
          <span className="activity-label">СОБЫТИЕ</span>
          {timestamp && <span className="activity-time">{fmtTime(timestamp)}</span>}
        </div>
        <div className="activity-content">{getString(payload, 'narrative')}</div>
      </div>
    )
  }

  if (event_type === 'message_sent') {
    const toId = getString(payload, 'to_id')
    const content = getString(payload, 'content')
    const response = getString(payload, 'response')
    const isPrivate = getBool(payload, 'private')
    return (
      <div className={`activity-item message${isPrivate ? ' private' : ''}`}>
        <div className="activity-item-header">
          <span className="activity-icon">{isPrivate ? '🔒' : '💬'}</span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          <span className="activity-arrow">→</span>
          <span className="activity-agent">{dn(toId, names)}</span>
          {timestamp && <span className="activity-time">{fmtTime(timestamp)}</span>}
        </div>
        {content && content !== 'msg' && <div className="activity-content">{content}</div>}
        {response && <div className="activity-response">{response}</div>}
      </div>
    )
  }

  return (
    <div className="activity-item generic">
      <span className="badge small">{event_type.replace(/_/g, ' ')}</span>
      <span className="activity-agent text-muted">{dn(agent_id, names)}</span>
      {timestamp && <span className="activity-time">{fmtTime(timestamp)}</span>}
    </div>
  )
}

export function ActivityFeed({
  events,
  names,
  selectedAgent,
  onClearFilter,
  focusDay,
}: Props) {
  const [openDocs, setOpenDocs] = useState<Record<string, boolean>>({})
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pausedRef = useRef(false)

  const filtered = useMemo(() => {
    const noIdle = events.filter((e) => e.event_type !== 'idle')
    if (!selectedAgent) return noIdle
    return noIdle.filter((event) => belongsToAgent(event, selectedAgent))
  }, [events, selectedAgent])

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const ta = toMillis(eventTimestamp(a))
      const tb = toMillis(eventTimestamp(b))
      if (!Number.isNaN(ta) && !Number.isNaN(tb) && ta !== tb) return ta - tb
      if (typeof a.round === 'number' && typeof b.round === 'number' && a.round !== b.round) {
        return a.round - b.round
      }
      return 0
    })
  }, [filtered])

  const grouped = useMemo(() => {
    const entries = buildEntries(sorted)
    return groupByDayAndHour(entries)
  }, [sorted])

  useEffect(() => {
    if (!pausedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [events.length, selectedAgent])

  useEffect(() => {
    if (!focusDay || !scrollRef.current) return
    const element = scrollRef.current.querySelector(`[data-day="${focusDay}"]`)
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [focusDay])

  const toggleDoc = (docId: string) => {
    if (!docId) return
    setOpenDocs((prev) => ({ ...prev, [docId]: !prev[docId] }))
  }

  return (
    <div className="activity-feed">
      <div className="activity-feed-header">
        <span>Активность{selectedAgent ? ` — ${dn(selectedAgent, names)}` : ''}</span>
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
        {grouped.map((group) => (
          <div key={group.dayKey} className="activity-day-group" data-day={group.dayKey}>
            <div className={`activity-day-label${focusDay === group.dayKey ? ' focused' : ''}`}>
              {group.label}
            </div>
            {group.items.map((item, idx) => {
              if (item.kind === 'hour') {
                return (
                  <div key={`${group.dayKey}-hour-${item.label}-${idx}`} className="activity-hour-label">
                    {item.label}
                  </div>
                )
              }
              if (item.kind === 'thread') {
                return (
                  <ThreadView
                    key={`${group.dayKey}-${item.thread.thread_id}-${idx}`}
                    thread={item.thread}
                    names={names}
                  />
                )
              }
              const docId = getString(item.event.payload, 'doc_id')
              return (
                <EventView
                  key={`${group.dayKey}-${item.event.event_type}-${item.event.agent_id}-${idx}`}
                  event={item.event}
                  names={names}
                  docExpanded={Boolean(openDocs[docId])}
                  onToggleDoc={toggleDoc}
                />
              )
            })}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
