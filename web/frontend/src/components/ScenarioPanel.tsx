import { useEffect, useMemo, useState } from 'react'
import { apiClient } from '../utils/apiClient'
import { Icon } from './Icons'

interface AgentEntry {
  id: string
  name?: string
  position?: string
  role?: string
  initial_reputation?: number
  capabilities?: string[]
  personality?: {
    biography?: string
  }
}

interface SimAgentConfig {
  agent_id?: string
  name?: string
  internal?: boolean
  capabilities?: string[]
  initial_title?: string
  org_id?: string | null
  zone_id?: string | null
  persona?: {
    summary?: string
    biography?: string
    interview?: Array<{ question?: string; answer?: string }>
    reflections?: string[]
  }
}

interface ScenarioData {
  id?: string
  name?: string
  title?: string
  description?: string
  governance?: string
  rounds?: number
  agents?: AgentEntry[]
  sim_config?: {
    title?: string
    description?: string
    ticks?: number
    runtime?: Record<string, unknown>
    governance?: Record<string, unknown>
    world?: Record<string, unknown>
    agents?: SimAgentConfig[]
  }
  [key: string]: unknown
}

interface Props {
  runName: string | null
}

function pretty(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2)
}

function firstLine(text: string | undefined, fallback: string): string {
  const trimmed = (text || '').trim()
  if (!trimmed) return fallback
  return trimmed.split('\n', 1)[0] || fallback
}

export function ScenarioPanel({ runName }: Props) {
  const [data, setData] = useState<ScenarioData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!runName) {
      setData(null)
      setError(null)
      return
    }

    setLoading(true)
    setError(null)

    apiClient.get(`/api/run/${encodeURIComponent(runName)}/scenario`)
      .then((r) => {
        if (r.status === 404) {
          setData(null)
          setError('not_found')
          setLoading(false)
          return null
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((json: ScenarioData | null) => {
        if (json) {
          setData(json)
          setLoading(false)
        }
      })
      .catch((err: Error) => {
        setError(err.message || 'Ошибка загрузки')
        setLoading(false)
      })
  }, [runName])

  const scenarioTitle = useMemo(() => {
    if (!data) return runName || 'Сценарий'
    return data.sim_config?.title || data.name || data.title || runName || 'Сценарий'
  }, [data, runName])

  if (!runName) {
    return (
      <div className="activity-feed">
        <div className="activity-feed-header">
          <span>Сценарий</span>
        </div>
        <div className="activity-feed-scroll">
          <div className="activity-feed-empty">
            <span className="text-muted">Откройте прогон, чтобы увидеть полный сценарий</span>
          </div>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="activity-feed">
        <div className="activity-feed-header">
          <span>Сценарий</span>
        </div>
        <div className="activity-feed-scroll">
          <div className="activity-feed-empty">
            <span className="text-muted">Загрузка...</span>
          </div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="activity-feed">
        <div className="activity-feed-header">
          <span>Сценарий</span>
        </div>
        <div className="activity-feed-scroll">
          <div className="activity-feed-empty">
            {error === 'not_found'
              ? <span className="text-muted">Конфигурация сценария недоступна для этого прогона</span>
              : <span className="badge danger">{error}</span>}
          </div>
        </div>
      </div>
    )
  }

  if (!data) return null

  const simConfig = data.sim_config
  const simAgents = Array.isArray(simConfig?.agents) ? simConfig.agents : []
  const runtime = simConfig?.runtime ?? {}
  const governance = simConfig?.governance ?? {}
  const world = simConfig?.world ?? {}

  return (
    <div className="activity-feed">
      <div className="activity-feed-header">
        <span>Сценарий</span>
      </div>
      <div className="activity-feed-scroll" style={{ padding: '0.5rem 0.75rem 0.75rem' }}>
        <div style={{ marginBottom: '0.75rem' }}>
          <div className="text-muted" style={{ fontSize: '0.58rem', marginBottom: '0.2rem' }}>Название</div>
          <div style={{ fontSize: '0.82rem', fontWeight: 700 }}>{scenarioTitle}</div>
        </div>

        {(data.description || simConfig?.description) && (
          <div style={{ marginBottom: '0.75rem' }}>
            <div className="text-muted" style={{ fontSize: '0.58rem', marginBottom: '0.25rem' }}>Описание</div>
            <div className="md" style={{ fontSize: '0.7rem', whiteSpace: 'pre-wrap' }}>
              {data.description || simConfig?.description}
            </div>
          </div>
        )}

        <div className="environment-summary-grid" style={{ marginBottom: '0.75rem' }}>
          <div className="environment-summary-card">
            <div className="environment-summary-label">Тики</div>
            <div className="environment-summary-value">{simConfig?.ticks ?? data.rounds ?? '—'}</div>
          </div>
          <div className="environment-summary-card">
            <div className="environment-summary-label">Агенты</div>
            <div className="environment-summary-value">{simAgents.length || data.agents?.length || 0}</div>
          </div>
          <div className="environment-summary-card">
            <div className="environment-summary-label">Старт мира</div>
            <div className="environment-summary-value" style={{ fontSize: '0.72rem' }}>{String(runtime.start_date ?? '—')}</div>
          </div>
        </div>

        <details className="md-details" open>
          <summary className="md-summary">Runtime и временная модель</summary>
          <pre className="prompt-text">{pretty(runtime)}</pre>
        </details>

        <details className="md-details" open>
          <summary className="md-summary">Governance и аудит</summary>
          <pre className="prompt-text">{pretty(governance)}</pre>
        </details>

        <details className="md-details" open>
          <summary className="md-summary">Мир: каналы, организации, дела, артефакты</summary>
          <pre className="prompt-text">{pretty(world)}</pre>
        </details>

        <details className="md-details" open>
          <summary className="md-summary">Агенты и полный бэкграунд ({simAgents.length})</summary>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }}>
            {simAgents.map((agent) => (
              <details key={agent.agent_id || agent.name} className="md-details" style={{ marginTop: 0 }}>
                <summary className="md-summary">
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
                    <Icon name="scenario" size={12} />
                    <span>{agent.name || agent.agent_id || 'Агент'}</span>
                  </span>
                  <span style={{ marginLeft: '0.5rem', color: 'var(--text-muted)' }}>
                    {firstLine(agent.initial_title, agent.internal ? 'внутренний' : 'внешний')}
                  </span>
                </summary>
                <div style={{ marginTop: '0.5rem', display: 'grid', gap: '0.5rem' }}>
                  <div className="prompt-section">
                    <div className="prompt-section-label">Паспорт</div>
                    <pre className="prompt-text">{pretty({
                      agent_id: agent.agent_id,
                      internal: agent.internal,
                      initial_title: agent.initial_title,
                      capabilities: agent.capabilities,
                      org_id: agent.org_id,
                      zone_id: agent.zone_id,
                    })}</pre>
                  </div>
                  <div className="prompt-section">
                    <div className="prompt-section-label">Persona summary</div>
                    <pre className="prompt-text">{agent.persona?.summary || '—'}</pre>
                  </div>
                  <div className="prompt-section">
                    <div className="prompt-section-label">Biография</div>
                    <pre className="prompt-text">{agent.persona?.biography || '—'}</pre>
                  </div>
                  <div className="prompt-section">
                    <div className="prompt-section-label">Интервью</div>
                    <pre className="prompt-text">{pretty(agent.persona?.interview ?? [])}</pre>
                  </div>
                  <div className="prompt-section">
                    <div className="prompt-section-label">Рефлексия</div>
                    <pre className="prompt-text">{pretty(agent.persona?.reflections ?? [])}</pre>
                  </div>
                </div>
              </details>
            ))}
          </div>
        </details>

        <details className="md-details">
          <summary className="md-summary">Полный ScenarioConfig JSON</summary>
          <pre className="prompt-text">{pretty(simConfig)}</pre>
        </details>
      </div>
    </div>
  )
}
