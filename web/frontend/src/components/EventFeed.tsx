import { useEffect, useRef } from 'react'
import type { SimEvent } from '../types'

const EVENT_ICONS: Record<string, string> = {
  message_sent: 'msg',
  graph_updated: 'link',
  reputation_modified: 'rep',
  case_opened: 'case',
  case_resolved: 'done',
  case_modified: 'edit',
  proposal_submitted: 'prop',
  evidence_added: 'e+',
  evidence_removed: 'e-',
  arbiter_approved: 'ok',
  arbiter_rejected: 'no',
  auto_transition: 'auto',
  world_event: 'world',
}

interface Props {
  events: SimEvent[]
  selectedAgent?: string | null
}

export function EventFeed({ events, selectedAgent }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events.length])

  const filtered = selectedAgent
    ? events.filter(
        (e) =>
          e.agent_id === selectedAgent ||
          e.payload.to_id === selectedAgent ||
          e.payload.target === selectedAgent
      )
    : events

  return (
    <div className="flex flex-col h-full">
      <div className="text-xs text-slate-400 px-2 py-1 border-b border-slate-700">
        События {selectedAgent ? `(${selectedAgent})` : ''}: {filtered.length}
      </div>
      <div className="flex-1 overflow-y-auto text-xs space-y-0.5 p-1">
        {filtered.map((e, i) => (
          <div
            key={i}
            className="flex gap-1 items-start rounded px-1 py-0.5 hover:bg-slate-800"
          >
            <span className="shrink-0 w-5 text-center">
              {EVENT_ICONS[e.event_type] ?? '•'}
            </span>
            <span className="text-slate-400 shrink-0 w-4">R{e.round}</span>
            <span className="text-slate-300 shrink-0 font-mono w-14 truncate">
              {e.agent_id || '—'}
            </span>
            <span className="text-slate-500 truncate">{e.event_type}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
