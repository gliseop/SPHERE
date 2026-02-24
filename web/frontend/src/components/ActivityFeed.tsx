import { useEffect, useRef } from 'react'
import type { SimEvent } from '../types'

interface Props {
  events: SimEvent[]
  names: Record<string, string>
  selectedAgent: string | null
  onClearFilter?: () => void
  focusRound?: number | null
}

function dn(id: string | undefined, names: Record<string, string>): string {
  if (!id) return ''
  return names[id] ?? id
}

function fmtTime(ts: string): string {
  try {
    const d = new Date(ts)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
}

function roleBadge(id: string): { label: string; cls: string } | null {
  if (id.startsWith('aud')) return { label: 'АУДИТОР', cls: 'accent' }
  if (id.startsWith('soc_journalist') || id.startsWith('soc_j')) return { label: 'ЖУРНАЛИСТ', cls: 'info' }
  if (id.startsWith('soc_activist') || id.startsWith('soc_a')) return { label: 'АКТИВИСТ', cls: 'info' }
  if (id.startsWith('fam_')) return { label: 'СЕМЬЯ', cls: 'warning' }
  return null
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

  useEffect(() => {
    if (focusRound == null || !scrollRef.current) return
    const el = scrollRef.current.querySelector(`[data-round="${focusRound}"]`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [focusRound])

  const filtered = selectedAgent
    ? events.filter((e) => {
        if (e.agent_id === selectedAgent) return true
        if (e.payload.to_id === selectedAgent) return true
        if (e.payload.target === selectedAgent) return true
        if (e.payload.owner_id === selectedAgent) return true
        if (e.payload.winner === selectedAgent) return true
        if (e.event_type === 'graph_updated') {
          if (e.payload.agent_a === selectedAgent || e.payload.agent_b === selectedAgent) return true
        }
        return false
      })
    : events

  const sorted = [...filtered].sort((a, b) => {
    const ra = a.round - b.round
    if (ra !== 0) return ra
    return (a.timestamp ?? '').localeCompare(b.timestamp ?? '')
  })

  // Group by day (from timestamp) or by round
  const grouped: { label: string; round: number; items: SimEvent[] }[] = []
  for (const e of sorted) {
    const dayLabel = e.timestamp ? fmtDay(e.timestamp) : `Раунд ${e.round}`
    const last = grouped[grouped.length - 1]
    if (!last || last.label !== dayLabel) {
      grouped.push({ label: dayLabel, round: e.round, items: [e] })
    } else {
      last.items.push(e)
    }
  }

  return (
    <div className="activity-feed">
      <div className="activity-feed-header">
        <span>
          Активность{selectedAgent ? ` — ${dn(selectedAgent, names)}` : ''}
        </span>
        {selectedAgent && onClearFilter && (
          <button className="btn-clipped small" onClick={onClearFilter}
            style={{ marginLeft: '0.5rem', padding: '0.15rem 0.5rem', fontSize: '0.6rem' }}>
            ✕ Сбросить
          </button>
        )}
      </div>
      <div ref={scrollRef} className="activity-feed-scroll"
        onMouseEnter={() => { pausedRef.current = true }}
        onMouseLeave={() => { pausedRef.current = false }}>
        {grouped.length === 0 && (
          <div className="activity-feed-empty">
            <span className="text-muted">Запустите прогон чтобы увидеть активность</span>
          </div>
        )}
        {grouped.map(({ label, round, items }) => (
          <div key={label} className="activity-round-group" data-round={round}>
            <div className={`activity-round-label${focusRound === round ? ' focused' : ''}`}>
              {label}
            </div>
            {items.map((e, i) => (
              <ActivityItem key={`${e.round}-${e.event_type}-${e.agent_id}-${i}`} event={e} names={names} />
            ))}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

function fmtDay(ts: string): string {
  try {
    const d = new Date(ts)
    if (isNaN(d.getTime())) return ''
    const days = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб']
    return `${days[d.getDay()]} ${d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })}`
  } catch { return '' }
}

function ActivityItem({ event, names }: { event: SimEvent; names: Record<string, string> }) {
  const { event_type, agent_id, payload, timestamp } = event
  const time = fmtTime(timestamp)
  const badge = roleBadge(agent_id)

  // ── World event ──
  if (event_type === 'world_event' && payload.narrative) {
    return (
      <div className="activity-item world">
        <div className="activity-item-header">
          <span className="activity-icon">📢</span>
          <span className="activity-label">СОБЫТИЕ</span>
          {time && <span className="activity-time">{time}</span>}
        </div>
        <div className="activity-content">{payload.narrative as string}</div>
      </div>
    )
  }

  // ── Self-reflection ──
  if (event_type === 'self_reflection') {
    const content = String(payload.content ?? '')
    return (
      <div className={`activity-item reflection${badge ? ' ' + badge.cls : ''}`}>
        <div className="activity-item-header">
          <span className="activity-icon">💭</span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          {badge && <span className={`badge ${badge.cls} small`}>{badge.label}</span>}
          {time && <span className="activity-time">{time}</span>}
        </div>
        {content && <div className="activity-content italic">{content}</div>}
      </div>
    )
  }

  // ── Message sent ──
  if (event_type === 'message_sent') {
    const toId = typeof payload.to_id === 'string' ? payload.to_id : ''
    const isPrivate = Boolean(payload.private)
    const content = typeof payload.content === 'string' ? payload.content : ''
    const response = typeof payload.response === 'string' ? payload.response : ''
    return (
      <div className={`activity-item message${isPrivate ? ' private' : ''}`}>
        <div className="activity-item-header">
          <span className="activity-icon">{isPrivate ? '🔒' : '💬'}</span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          <span className="activity-arrow">{isPrivate ? '⇢' : '→'}</span>
          <span className="activity-agent">{dn(toId, names)}</span>
          {isPrivate && <span className="badge accent small">ПРИВ</span>}
          {badge && <span className={`badge ${badge.cls} small`}>{badge.label}</span>}
          {time && <span className="activity-time">{time}</span>}
        </div>
        {content && <div className="activity-content">{content}</div>}
        {response && (
          <div className="activity-response">
            <span className="activity-response-label">↳ {dn(toId, names)}:</span>
            <span>{response}</span>
          </div>
        )}
      </div>
    )
  }

  // ── Case opened ──
  if (event_type === 'case_opened') {
    const caseId = String(payload.case_id ?? '')
    const caseType = String(payload.case_type ?? '').toUpperCase()
    const title = String(payload.title ?? '')
    const desc = String(payload.description ?? '')
    return (
      <div className="activity-item case-opened">
        <div className="activity-item-header">
          <span className="activity-icon">📂</span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          <span className="badge primary small">{caseId}</span>
          <span className="badge small">{caseType}</span>
          {badge && <span className={`badge ${badge.cls} small`}>{badge.label}</span>}
          {time && <span className="activity-time">{time}</span>}
        </div>
        <div className="activity-case-title">{title}</div>
        {desc && <div className="activity-content text-muted">{desc}</div>}
      </div>
    )
  }

  // ── Proposal submitted ──
  if (event_type === 'proposal_submitted') {
    const caseId = String(payload.case_id ?? '')
    const propId = String(payload.proposal_id ?? '')
    const content = String(payload.content ?? '')
    const price = typeof payload.price === 'number'
      ? new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(payload.price)
      : null
    return (
      <div className="activity-item proposal">
        <div className="activity-item-header">
          <span className="activity-icon">📋</span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          <span className="badge primary small">{caseId}</span>
          <span className="badge small">{propId}</span>
          {price && <span className="badge success small">{price}</span>}
          {time && <span className="activity-time">{time}</span>}
        </div>
        {content && <div className="activity-content">{content}</div>}
      </div>
    )
  }

  // ── Case resolved ──
  if (event_type === 'case_resolved') {
    const caseId = String(payload.case_id ?? '')
    const decision = String(payload.decision ?? '')
    const justification = String(payload.justification ?? '')
    return (
      <div className="activity-item case-resolved">
        <div className="activity-item-header">
          <span className="activity-icon">⚖️</span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          <span className="badge primary small">{caseId}</span>
          <span className="badge accent small">РЕШЕНИЕ</span>
          {time && <span className="activity-time">{time}</span>}
        </div>
        <div className="activity-case-title">{decision}</div>
        {justification && <div className="activity-content text-muted">{justification}</div>}
      </div>
    )
  }

  // ── Report filed ──
  if (event_type === 'report_filed') {
    const rec = String(payload.recommendation ?? '')
    const caseId = String(payload.case_id ?? '')
    const assessment = String(payload.assessment ?? '')
    const recLabel = rec === 'tribunal' ? '⚠️ ТРИБУНАЛ' : rec === 'frozen' ? '❄️ ЗАМОРОЗКА' : '👁️ НАБЛЮДЕНИЕ'
    return (
      <div className={`activity-item report ${rec}`}>
        <div className="activity-item-header">
          <span className="activity-icon">📝</span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          <span className="badge primary small">{caseId}</span>
          <span className="badge accent small">{recLabel}</span>
          {badge && <span className={`badge ${badge.cls} small`}>{badge.label}</span>}
          {time && <span className="activity-time">{time}</span>}
        </div>
        {assessment && <div className="activity-content">{assessment}</div>}
      </div>
    )
  }

  // ── Case note ──
  if (event_type === 'case_note') {
    const caseId = String(payload.case_id ?? '')
    const content = String(payload.content ?? '')
    return (
      <div className="activity-item case-note">
        <div className="activity-item-header">
          <span className="activity-icon">📌</span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          <span className="badge primary small">{caseId}</span>
          <span className="badge small">ЗАПИСЬ</span>
          {badge && <span className={`badge ${badge.cls} small`}>{badge.label}</span>}
          {time && <span className="activity-time">{time}</span>}
        </div>
        {content && <div className="activity-content">{content}</div>}
      </div>
    )
  }

  // ── Move to ──
  if (event_type === 'move_to') {
    const loc = String(payload.location_name ?? payload.location ?? '')
    return (
      <div className="activity-item move">
        <div className="activity-item-header">
          <span className="activity-icon">📍</span>
          <span className="activity-agent">{dn(agent_id, names)}</span>
          <span className="activity-arrow">→</span>
          <span>{loc}</span>
          {time && <span className="activity-time">{time}</span>}
        </div>
      </div>
    )
  }

  // ── Graph updated ──
  if (event_type === 'graph_updated') {
    const agentA = typeof payload.agent_a === 'string' ? payload.agent_a : ''
    const agentB = typeof payload.agent_b === 'string' ? payload.agent_b : ''
    const delta = typeof payload.delta === 'number' ? payload.delta : 0
    const sign = delta >= 0 ? '+' : ''
    return (
      <div className="activity-item graph-link">
        <span className="activity-icon">🔗</span>
        <span className="activity-agent">{dn(agentA, names)}</span>
        <span className="activity-arrow">↔</span>
        <span className="activity-agent">{dn(agentB, names)}</span>
        <span className={`activity-delta ${delta >= 0 ? 'success' : 'danger'}`}>
          {sign}{delta.toFixed(2)}
        </span>
        {time && <span className="activity-time">{time}</span>}
      </div>
    )
  }

  // ── Reputation modified ──
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
          <span className="activity-agent">{dn(target, names)}</span>
          <span className={`activity-delta ${delta >= 0 ? 'success' : 'danger'}`}>
            {sign}{delta.toFixed(2)}
          </span>
          <span className="badge small">РЕП</span>
          {time && <span className="activity-time">{time}</span>}
        </div>
        {reason && <div className="activity-content text-muted">{reason}</div>}
      </div>
    )
  }

  // ── Fallback ──
  return (
    <div className="activity-item generic">
      <span className="badge small">{event_type.replace(/_/g, ' ').toUpperCase().slice(0, 16)}</span>
      <span className="activity-agent text-muted">{dn(agent_id, names)}</span>
      {time && <span className="activity-time">{time}</span>}
    </div>
  )
}
