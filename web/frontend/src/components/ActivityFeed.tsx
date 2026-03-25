import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SimEvent, SimMeta, ThreadGroup } from '../types'
import { getString, getBool, getNumber } from '../utils/payload'
import { toDayKey, formatDayLabel } from '../utils/time'
import { Markdown } from './Markdown'
import { apiClient } from '../utils/apiClient'
import { Icon, type IconName } from './Icons'

interface Props {
  events: SimEvent[]
  names: Record<string, string>
  mode?: 'idle' | 'playback' | 'live' | 'snapshot'
  meta?: SimMeta | null
  selectedAgent: string | null
  onClearFilter?: () => void
  focusDay?: string | null
  runName?: string | null
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

function isLongText(text: string): boolean {
  if (!text) return false
  if (text.length > 800) return true
  const lines = text.split('\n').length
  return lines > 10
}

function CollapsibleMarkdown({ content, className }: { content: string; className?: string }) {
  if (!isLongText(content)) return <Markdown content={content} className={className} />
  const preview = content.replace(/\s+/g, ' ').trim().slice(0, 180)
  return (
    <details className="md-details">
      <summary className="md-summary">
        {preview}{preview.length < content.length ? '…' : ''}
      </summary>
      <Markdown content={content} className={className} />
    </details>
  )
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  case_opened: 'дело открыто',
  case_resolved: 'дело закрыто',
  case_modified: 'дело изменено',
  case_frozen: 'дело заморожено',
  funds_transferred: 'перевод средств',
  evidence_added: 'улика добавлена',
  evidence_removed: 'улика удалена',
  reputation_modified: 'репутация изменена',
  reputation_frozen: 'репутация заморожена',
  graph_updated: 'связь обновлена',
  complaint_filed: 'жалоба подана',
  tribunal_formed: 'трибунал созван',
  tribunal_verdict: 'вердикт трибунала',
  vote_cast: 'голос отдан',
  agent_moved: 'агент перемещён',
  need_created: 'потребность создана',
  proposal_submitted: 'предложение подано',
  note_added: 'заметка добавлена',
  report_filed: 'отчёт подан',
  position_promoted: 'повышение',
  arbiter_op_failed: 'ошибка арбитра',
  arbiter_approved: 'арбитр одобрил',
  arbiter_rejected: 'арбитр отклонил',
  agent_error: 'ошибка агента',
  llm_call: 'вызов LLM',
}

function eventTypeLabel(eventType: string): string {
  return EVENT_TYPE_LABELS[eventType] ?? eventType.replace(/_/g, ' ')
}

function channelIcon(channel: string): IconName {
  if (channel === 'phone') return 'phone'
  if (channel === 'email') return 'mail'
  if (channel === 'face_to_face') return 'face'
  if (channel === 'official_doc') return 'document'
  return 'chat'
}

function eventTimestamp(event: SimEvent): string {
  if (typeof event.simulated_timestamp === 'string' && event.simulated_timestamp) return event.simulated_timestamp
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

const MAX_VISIBLE_EVENTS = 2_000

function takeTail(
  events: SimEvent[],
  predicate: (event: SimEvent) => boolean,
  limit: number,
): SimEvent[] {
  if (limit <= 0) return []
  const out: SimEvent[] = []
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]
    if (!predicate(e)) continue
    out.push(e)
    if (out.length >= limit) break
  }
  out.reverse()
  return out
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
        <span className="channel-badge"><Icon name={channelIcon(thread.channel)} size={12} /> {thread.channel}</span>
        <span className="thread-participants">
          {thread.participants.map((id) => dn(id, names)).join(' • ')}
        </span>
        {thread.private && <span className="private-badge">приватный</span>}
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
              <div className="thread-bubble-content">
                {content
                  ? <CollapsibleMarkdown content={content} />
                  : <span className="text-muted">—</span>}
              </div>
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
      {expanded && (
        <div className="document-content">
          {content ? <Markdown content={content} /> : <span className="text-muted">—</span>}
        </div>
      )}
    </div>
  )
}

function PromptModal({
  runName,
  agentId,
  round,
  timestamp,
  names,
  onClose,
}: {
  runName: string
  agentId: string
  round: number | null
  timestamp?: string
  names: Record<string, string>
  onClose: () => void
}) {
  const [data, setData] = useState<{ system_prompt: string; user_prompt: string; response: string } | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    setData(null)
    const params = new URLSearchParams()
    if (agentId) params.set('agent_id', agentId)
    if (round !== null) params.set('round', String(round))
    if (timestamp) params.set('timestamp', timestamp)
    params.set('limit', '1')
    apiClient.get(`/api/run/${runName}/prompts?${params}`)
      .then((r) => r.ok ? r.json() : [])
      .then((arr) => {
        if (Array.isArray(arr) && arr.length > 0) {
          const p = arr[0].payload ?? arr[0]
          setData({
            system_prompt: p.system_prompt ?? '',
            user_prompt: p.user_prompt ?? '',
            response: p.response ?? '',
          })
        }
      })
      .finally(() => setLoading(false))
  }, [runName, agentId, round, timestamp])

  return (
    <div className="prompt-modal-overlay" onClick={onClose}>
      <div className="prompt-modal" onClick={(e) => e.stopPropagation()}>
        <div className="prompt-modal-header">
          <span>Промпт — {dn(agentId, names)}{round !== null ? ` (R${round})` : ''}</span>
          <button className="btn-clipped small" onClick={onClose}><Icon name="close" size={14} /></button>
        </div>
        {loading ? (
          <div className="text-muted" style={{ padding: '1rem' }}>Загрузка...</div>
        ) : data ? (
          <div className="prompt-modal-content">
            <div className="prompt-section">
              <div className="prompt-section-label">Системный промпт</div>
              <pre className="prompt-text">{data.system_prompt}</pre>
            </div>
            <div className="prompt-section">
              <div className="prompt-section-label">Пользовательский промпт</div>
              <pre className="prompt-text">{data.user_prompt}</pre>
            </div>
            <div className="prompt-section">
              <div className="prompt-section-label">Ответ модели</div>
              <pre className="prompt-text">{data.response}</pre>
            </div>
          </div>
        ) : (
          <div className="text-muted" style={{ padding: '1rem' }}>Промпт не найден</div>
        )}
      </div>
    </div>
  )
}

function EventDetailsModal({
  event,
  names,
  onClose,
}: {
  event: SimEvent
  names: Record<string, string>
  onClose: () => void
}) {
  return (
    <div className="prompt-modal-overlay" onClick={onClose}>
      <div className="prompt-modal" onClick={(e) => e.stopPropagation()}>
        <div className="prompt-modal-header">
          <span>{eventTypeLabel(event.event_type)} — {dn(event.agent_id, names) || 'system'}</span>
          <button className="btn-clipped small" onClick={onClose}><Icon name="close" size={14} /></button>
        </div>
        <div className="prompt-modal-content">
          <div className="prompt-section">
            <div className="prompt-section-label">Время мира</div>
            <pre className="prompt-text">{event.simulated_timestamp || `${event.simulated_date || '—'} ${event.simulated_time || ''}`.trim() || '—'}</pre>
          </div>
          <div className="prompt-section">
            <div className="prompt-section-label">Время записи</div>
            <pre className="prompt-text">{event.timestamp || '—'}</pre>
          </div>
          <div className="prompt-section">
            <div className="prompt-section-label">Payload</div>
            <pre className="prompt-text">{JSON.stringify(event.payload ?? {}, null, 2)}</pre>
          </div>
        </div>
      </div>
    </div>
  )
}

function EventView({
  event,
  names,
  docExpanded,
  onToggleDoc,
  runName,
  onShowPrompt,
  onOpenDetails,
}: {
  event: SimEvent
  names: Record<string, string>
  docExpanded: boolean
  onToggleDoc: (docId: string) => void
  runName?: string | null
  onShowPrompt?: (agentId: string, round: number | null, timestamp?: string) => void
  onOpenDetails: (event: SimEvent) => void
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
      <div className="activity-item world activity-item-button" onClick={() => onOpenDetails(event)} role="button" tabIndex={0}>
        <div className="activity-item-header">
          <span className="activity-icon"><Icon name="world" size={16} /></span>
          <span className="activity-label">СОБЫТИЕ</span>
          {timestamp && <span className="activity-time">{fmtTime(timestamp)}</span>}
        </div>
        <div className="activity-content">{getString(payload, 'description') || getString(payload, 'narrative') || '—'}</div>
      </div>
    )
  }

  if (event_type === 'message_sent') {
    const toId = getString(payload, 'to_id')
    const content = getString(payload, 'content')
    const response = getString(payload, 'response')
    const isPrivate = getBool(payload, 'private')
    const showContent = Boolean(content && content !== 'msg')
    const showResponse = Boolean(response)
    return (
      <div className={`activity-item message activity-item-button${isPrivate ? ' private' : ''}`} onClick={() => onOpenDetails(event)} role="button" tabIndex={0}>
        <div className="activity-item-header">
          <span className="activity-icon"><Icon name={isPrivate ? 'lock' : 'message'} size={16} /></span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          <span className="activity-arrow">к</span>
          <span className="activity-agent">{dn(toId, names)}</span>
          {timestamp && <span className="activity-time">{fmtTime(timestamp)}</span>}
        </div>
        {showContent && (
          <div className="activity-content">
            <CollapsibleMarkdown content={content} />
          </div>
        )}
        {showResponse && (
          <div className="activity-response">
            <CollapsibleMarkdown content={response} />
          </div>
        )}
        {!showContent && !showResponse && (
          <div className="activity-content text-muted">
            (сообщение без сохранённого текста)
          </div>
        )}
      </div>
    )
  }

  if (event_type === 'llm_call') {
    const callType = getString(payload, 'call_type')
    const sysLen = getNumber(payload, 'system_prompt_len') ?? 0
    const usrLen = getNumber(payload, 'user_prompt_len') ?? 0
    const respLen = getNumber(payload, 'response_len') ?? 0
    return (
      <div className="activity-item generic llm-call">
        <span className="badge small accent">{callType === 'reply' ? 'LLM ответ' : 'LLM ход'}</span>
        <span className="activity-agent text-muted">{dn(agent_id, names)}</span>
        <span className="text-muted" style={{ fontSize: '0.55rem' }}>
          sys:{sysLen} usr:{usrLen} resp:{respLen}
        </span>
        {timestamp && <span className="activity-time">{fmtTime(timestamp)}</span>}
        <button className="prompt-view-btn" onClick={() => onOpenDetails(event)} title="Детали события">
          <Icon name="open" size={12} />
        </button>
        {runName && onShowPrompt && (
          <button
            className="prompt-view-btn"
            onClick={() => onShowPrompt(agent_id, event.round ?? null, timestamp)}
            title="Посмотреть полный промпт"
          >
            <Icon name="prompt" size={12} />
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="activity-item generic activity-item-button" onClick={() => onOpenDetails(event)} role="button" tabIndex={0}>
      <span className="badge small">{eventTypeLabel(event_type)}</span>
      <span className="activity-agent text-muted">{dn(agent_id, names)}</span>
      {timestamp && <span className="activity-time">{fmtTime(timestamp)}</span>}
      <span className="activity-inline-icon"><Icon name="open" size={12} /></span>
    </div>
  )
}

export function ActivityFeed({
  events,
  names,
  mode,
  meta,
  selectedAgent,
  onClearFilter,
  focusDay,
  runName,
}: Props) {
  const [openDocs, setOpenDocs] = useState<Record<string, boolean>>({})
  const [promptTarget, setPromptTarget] = useState<{ agentId: string; round: number | null; timestamp?: string } | null>(null)
  const [eventTarget, setEventTarget] = useState<SimEvent | null>(null)
  const [visibleLimit, setVisibleLimit] = useState(250)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pausedRef = useRef(false)

  const handleShowPrompt = useCallback((agentId: string, round: number | null, timestamp?: string) => {
    setPromptTarget({ agentId, round, timestamp })
  }, [])

  const visible = useMemo(() => {
    const base = (e: SimEvent) => e.event_type !== 'idle' && e.event_type !== 'reputation_snapshot'
    if (!selectedAgent) return takeTail(events, base, MAX_VISIBLE_EVENTS)
    return takeTail(
      events,
      (e) => base(e) && belongsToAgent(e, selectedAgent),
      MAX_VISIBLE_EVENTS,
    )
  }, [events, selectedAgent])

  useEffect(() => {
    setVisibleLimit(250)
  }, [selectedAgent, runName])

  const windowedVisible = useMemo(
    () => (visible.length > visibleLimit ? visible.slice(-visibleLimit) : visible),
    [visible, visibleLimit],
  )

  const grouped = useMemo(() => {
    const entries = buildEntries(windowedVisible)
    return groupByDayAndHour(entries)
  }, [windowedVisible])

  useEffect(() => {
    if (!pausedRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [visible.length, selectedAgent])

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

  const handleCopyTrace = useCallback(async () => {
    if (!runName || !selectedAgent) return
    const res = await apiClient.get(`/api/run/${encodeURIComponent(runName)}/trace.md?agent_id=${encodeURIComponent(selectedAgent)}`)
    if (!res.ok) {
      window.alert('Не удалось получить trace в Markdown')
      return
    }
    const text = await res.text()
    await navigator.clipboard.writeText(text)
  }, [runName, selectedAgent])

  const emptyText = (() => {
    if (selectedAgent) return 'Нет событий по выбранному агенту'
    if (mode === 'live') return meta ? 'Live подключён — ждём события…' : 'Подключение к Live…'
    if (mode === 'playback') return meta ? 'Воспроизведение — нет событий' : 'Загрузка…'
    if (mode === 'snapshot') return meta ? 'Финальное состояние прогона — событий в текущем окне нет' : 'Загрузка snapshot…'
    return 'Запустите прогон чтобы увидеть активность'
  })()

  return (
    <div className="activity-feed">
      <div className="activity-feed-header">
        <span>
          Активность{selectedAgent ? ` — ${dn(selectedAgent, names)}` : ''}
          <span className="text-muted" style={{ fontSize: '0.6rem', marginLeft: '0.5rem' }}>
            {visible.length > windowedVisible.length ? `показаны последние ${windowedVisible.length} из ${visible.length}` : `${visible.length}`}
          </span>
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          {selectedAgent && runName && (
            <button
              className="btn-clipped small"
              onClick={() => { void handleCopyTrace() }}
              style={{ padding: '0.15rem 0.5rem', fontSize: '0.6rem' }}
              title="Копировать trace выбранного агента в Markdown"
            >
              <Icon name="copy" size={12} />
            </button>
          )}
          {selectedAgent && onClearFilter && (
            <button
              className="btn-clipped small"
              onClick={onClearFilter}
              style={{ padding: '0.15rem 0.5rem', fontSize: '0.6rem' }}
            >
              Сбросить
            </button>
          )}
        </div>
      </div>
      <div
        ref={scrollRef}
        className="activity-feed-scroll"
        onMouseEnter={() => { pausedRef.current = true }}
        onMouseLeave={() => { pausedRef.current = false }}
      >
        {visible.length > windowedVisible.length && (
          <div style={{ padding: '0.25rem 0.75rem' }}>
            <button className="btn-clipped small" onClick={() => setVisibleLimit((limit) => limit + 250)}>
              Загрузить ещё
            </button>
          </div>
        )}
        {grouped.length === 0 && (
          <div className="activity-feed-empty">
            <span className="text-muted">{emptyText}</span>
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
                  runName={runName}
                  onShowPrompt={handleShowPrompt}
                  onOpenDetails={setEventTarget}
                />
              )
            })}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      {eventTarget && (
        <EventDetailsModal event={eventTarget} names={names} onClose={() => setEventTarget(null)} />
      )}
      {promptTarget && runName && (
        <PromptModal
          runName={runName}
          agentId={promptTarget.agentId}
          round={promptTarget.round}
          timestamp={promptTarget.timestamp}
          names={names}
          onClose={() => setPromptTarget(null)}
        />
      )}
    </div>
  )
}
