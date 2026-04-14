import { useMemo, useState } from 'react'
import type { GraphEdge, GraphNode, SimEvent } from '../types'
import {
  SUSPICIOUS_THRESHOLD,
  agentRoleClass,
  agentRoleLabel,
  isGovernanceAgentId,
} from '../constants'
import { getNumber } from '../utils/payload'
import { Icon } from './Icons'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
  selectedNode: string | null
  names: Record<string, string>
  onSelect: (id: string) => void
  scenarioConfig?: Record<string, unknown> | null
}

function connectionCount(id: string, edges: GraphEdge[]): number {
  return edges.filter((e) => e.source === id || e.target === id).length
}

function repTooltip(node: GraphNode): string {
  if (node.has_reputation === false) return 'Репутация не применяется к этому агенту'
  const frozen = node.reputation_frozen ? 'заморожена' : 'активна'
  const title = node.position_title ? `\nДолжность: ${node.position_title}` : ''
  const next = (typeof node.next_position_threshold === 'number' && node.next_position_title)
    ? `\nСледующая: ${node.next_position_title} (≥ ${node.next_position_threshold})`
    : ''
  return `Репутация: ${node.reputation.toFixed(1)} (${frozen})${title}${next}`
}

interface AgentProfile {
  id: string
  name?: string
  position?: string
  personality?: {
    biography?: string
    hexaco?: Record<string, number>
    dark_triad?: Record<string, number>
    neutralization_techniques?: string[]
  }
  connections?: Array<{ target_id: string; relation: string; strength: number }>
}

const HEXACO_LABELS: Record<string, string> = {
  honesty_humility: 'Честность-скромность',
  emotionality: 'Эмоциональность',
  extraversion: 'Экстраверсия',
  agreeableness: 'Доброжелательность',
  conscientiousness: 'Добросовестность',
  openness: 'Открытость опыту',
}

const DARK_TRIAD_LABELS: Record<string, string> = {
  narcissism: 'Нарциссизм',
  machiavellianism: 'Макиавеллизм',
  psychopathy: 'Психопатия',
}

const TECHNIQUE_LABELS: Record<string, string> = {
  denial_of_injury: 'Отрицание ущерба',
  denial_of_victim: 'Отрицание жертвы',
  condemnation_of_condemners: 'Осуждение осуждающих',
  appeal_to_higher_loyalties: 'Апелляция к высшим ценностям',
  denial_of_responsibility: 'Отрицание ответственности',
  everyone_does_it: '«Все так делают»',
  claim_of_entitlement: 'Претензия на право',
  defense_of_necessity: 'Защита необходимостью',
}

function AgentProfilePanel({ profile, names }: { profile: AgentProfile; names: Record<string, string> }) {
  const p = profile.personality
  if (!p) return null

  return (
    <div className="agent-profile-panel">
      {profile.position && (
        <div className="agent-profile-row">
          <span className="agent-profile-label">Должность</span>
          <span>{profile.position}</span>
        </div>
      )}
      {p.biography && (
        <div className="agent-profile-section">
          <div className="agent-profile-label">Биография</div>
          <div className="agent-profile-bio">{p.biography}</div>
        </div>
      )}
      {p.hexaco && (
        <div className="agent-profile-section">
          <div className="agent-profile-label">HEXACO</div>
          <div className="agent-profile-traits">
            {Object.entries(p.hexaco).map(([k, v]) => (
              <div key={k} className="agent-profile-trait">
                <span>{HEXACO_LABELS[k] ?? k}</span>
                <span className="agent-profile-trait-value">{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {p.dark_triad && (
        <div className="agent-profile-section">
          <div className="agent-profile-label">Тёмная триада</div>
          <div className="agent-profile-traits">
            {Object.entries(p.dark_triad).map(([k, v]) => (
              <div key={k} className="agent-profile-trait">
                <span>{DARK_TRIAD_LABELS[k] ?? k}</span>
                <span className="agent-profile-trait-value">{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {p.neutralization_techniques && p.neutralization_techniques.length > 0 && (
        <div className="agent-profile-section">
          <div className="agent-profile-label">Техники нейтрализации</div>
          <div className="agent-profile-techniques">
            {p.neutralization_techniques.map((t) => (
              <span key={t} className="badge small">{TECHNIQUE_LABELS[t] ?? t}</span>
            ))}
          </div>
        </div>
      )}
      {profile.connections && profile.connections.length > 0 && (
        <div className="agent-profile-section">
          <div className="agent-profile-label">Связи</div>
          <div className="agent-profile-connections">
            {profile.connections.map((c, i) => (
              <div key={i} className="agent-profile-connection">
                <span>{names[c.target_id] ?? c.target_id}</span>
                <span className="text-muted">{c.relation}</span>
                <span className={c.strength >= SUSPICIOUS_THRESHOLD ? 'danger' : ''}>{c.strength.toFixed(1)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export function AgentList({ nodes, edges, events, selectedNode, names, onSelect, scenarioConfig }: Props) {
  const [expandedRep, setExpandedRep] = useState<string | null>(null)

  const repHistoryMap = useMemo(() => {
    const map = new Map<string, SimEvent[]>()
    for (const e of events) {
      if (e.event_type !== 'reputation_snapshot') continue
      let arr = map.get(e.agent_id)
      if (!arr) {
        arr = []
        map.set(e.agent_id, arr)
      }
      arr.push(e)
    }
    // Обрезаем до 40 последних
    for (const [key, arr] of map) {
      if (arr.length > 40) map.set(key, arr.slice(-40))
    }
    return map
  }, [events])

  const agentProfiles = useMemo(() => {
    if (!scenarioConfig) return new Map<string, AgentProfile>()
    const agents = (scenarioConfig as { agents?: AgentProfile[] }).agents
    if (!Array.isArray(agents)) return new Map<string, AgentProfile>()
    const map = new Map<string, AgentProfile>()
    for (const a of agents) {
      if (a.id) map.set(a.id, a)
    }
    return map
  }, [scenarioConfig])

  if (nodes.length === 0) {
    return (
      <div className="agent-list-empty">
        <span className="text-muted">Агенты появятся при запуске прогона</span>
      </div>
    )
  }

  const sorted = [...nodes].sort((a, b) => {
    const ar = a.has_reputation === false ? Number.NEGATIVE_INFINITY : a.reputation
    const br = b.has_reputation === false ? Number.NEGATIVE_INFINITY : b.reputation
    return br - ar
  })

  const selectedProfile = selectedNode && !isGovernanceAgentId(selectedNode)
    ? agentProfiles.get(selectedNode) ?? null
    : null

  return (
    <div className="agent-list">
      <div className="agent-list-header">Агенты ({nodes.length})</div>
      {sorted.map((node) => {
        const name = names[node.id] ?? node.id
        const hasRep = node.has_reputation !== false
        const target = typeof node.next_position_threshold === 'number'
          ? node.next_position_threshold
          : null
        const barMax = hasRep
          ? (target && target > 0 ? target : Math.max(node.reputation, 50))
          : 1
        const repPct = hasRep
          ? Math.max(0, Math.min(100, (node.reputation / barMax) * 100))
          : 0
        const suspicious = edges.some(
          (e) => (e.source === node.id || e.target === node.id) && e.strength >= SUSPICIOUS_THRESHOLD
        )
        const isExpanded = expandedRep === node.id

        const repHistory = repHistoryMap.get(node.id) ?? []

        const totalDelta = repHistory.reduce(
          (sum, ev) => sum + (getNumber(ev.payload, 'delta') ?? 0),
          0,
        )

        return (
          <div key={node.id}>
            <div
              className={`agent-list-item${selectedNode === node.id ? ' selected' : ''}${suspicious ? ' suspicious' : ''}`}
              onClick={() => onSelect(node.id === selectedNode ? '' : node.id)}
            >
              <div className="agent-list-row">
                <span className={`agent-dot ${agentRoleClass(node.id)}`} />
                <span className="agent-list-name">{name}</span>
                <span className={`badge ${agentRoleClass(node.id)} small`}>{agentRoleLabel(node.id)}</span>
              </div>
              <div className="agent-list-row" style={{ gap: '0.5rem', marginTop: '3px' }}>
                <div className="rep-bar" title={repTooltip(node)}>
                  {hasRep ? (
                    <div
                      className={`rep-bar-fill ${node.reputation_frozen ? 'danger' : ''}`}
                      style={{ width: `${repPct}%` }}
                    />
                  ) : (
                    <div className="rep-bar-fill muted" style={{ width: '100%' }} />
                  )}
                </div>
                <span className="agent-list-rep">
                  {hasRep ? node.reputation.toFixed(1) : '—'}
                </span>
                <span className="text-muted" style={{ fontSize: '0.65rem' }}>
                  {connectionCount(node.id, edges)} св.
                </span>
                {repHistory.length > 0 && (
                  <button
                    className="rep-history-toggle"
                    onClick={(e) => {
                      e.stopPropagation()
                      setExpandedRep(isExpanded ? null : node.id)
                    }}
                    title="История репутации"
                  >
                    <Icon name={isExpanded ? 'chevronUp' : 'chevronDown'} size={12} />
                  </button>
                )}
              </div>
            </div>

            {isExpanded && repHistory.length > 0 && (
              <div className="rep-history">
                <div className="rep-history-summary">
                  {repHistory.length} изм., {'\u0394'} total:{' '}
                  <span className={totalDelta >= 0 ? 'rep-history-delta success' : 'rep-history-delta danger'}>
                    {totalDelta >= 0 ? '+' : ''}{totalDelta.toFixed(2)}
                  </span>
                </div>
                <div className="rep-history-scroll">
                  {repHistory.map((ev, i) => {
                    const delta = getNumber(ev.payload, 'delta') ?? 0
                    const cases = getNumber(ev.payload, 'cases_resolved')
                    const complaints = getNumber(ev.payload, 'complaints_received')
                    const sign = delta >= 0 ? '+' : ''
                    return (
                      <div key={i} className="rep-history-item">
                        <span className="rep-history-round">
                          {typeof ev.round === 'number'
                            ? `R${ev.round}`
                            : (() => {
                                const ms = ev.timestamp ? Date.parse(ev.timestamp) : NaN
                                if (!Number.isFinite(ms)) return '--:--'
                                const d = new Date(ms)
                                return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
                              })()}
                        </span>
                        <span className={`rep-history-delta ${delta >= 0 ? 'success' : 'danger'}`}>
                          {sign}{delta.toFixed(2)}
                        </span>
                        {(cases !== null || complaints !== null) && (
                          <span className="rep-history-reason">
                            {cases !== null ? `дел: ${cases}` : ''}
                            {complaints !== null ? `  жалоб: ${complaints}` : ''}
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )
      })}

      {selectedProfile && (
        <AgentProfilePanel profile={selectedProfile} names={names} />
      )}
    </div>
  )
}
