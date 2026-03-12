import { useEffect, useState } from 'react'
import { apiClient } from '../utils/apiClient'

interface AgentEntry {
  id: string
  name?: string
  position?: string
  role?: string
  initial_reputation?: number
}

interface ScenarioData {
  id?: string
  name?: string
  title?: string
  description?: string
  narrative_context?: string
  scenario?: string
  scenario_template?: string
  governance?: string
  governance_mode?: string
  rounds?: number
  seed?: number | null
  agents?: AgentEntry[]
  [key: string]: unknown
}

interface Props {
  runName: string | null
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

  if (!runName) {
    return (
      <div className="activity-feed">
        <div className="activity-feed-header">
          <span>Сценарий</span>
        </div>
        <div className="activity-feed-scroll">
          <div className="activity-feed-empty">
            <span className="text-muted">Запустите прогон чтобы увидеть сценарий</span>
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
            {error === 'not_found' ? (
              <span className="text-muted">Конфигурация сценария недоступна для этого прогона</span>
            ) : (
              <span className="badge danger">{error}</span>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (!data) return null

  const title = data.name
    || data.title
    || (data.narrative_context ? data.narrative_context.split('\n')[0].slice(0, 120) : '')
    || data.id
    || runName

  const agents = Array.isArray(data.agents) ? data.agents : []
  const description = data.description || data.narrative_context || ''
  const scenarioBadge = data.scenario || data.scenario_template
  const governanceBadge = data.governance || data.governance_mode

  return (
    <div className="activity-feed">
      <div className="activity-feed-header">
        <span>Сценарий</span>
      </div>
      <div className="activity-feed-scroll" style={{ padding: '0.5rem' }}>
        {/* Название */}
        <div style={{ marginBottom: '0.5rem' }}>
          <div className="text-muted" style={{ fontSize: '0.6rem', marginBottom: '0.15rem' }}>Название</div>
          <div style={{ fontSize: '0.75rem', fontWeight: 600 }}>{title}</div>
        </div>

        {/* Шаблон и режим */}
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
          {scenarioBadge && (
            <span className="badge small">{scenarioBadge}</span>
          )}
          {governanceBadge && (
            <span className="badge small">{governanceBadge}</span>
          )}
          {typeof data.rounds === 'number' && (
            <span className="badge small">Раундов: {data.rounds}</span>
          )}
          {data.seed !== undefined && data.seed !== null && (
            <span className="badge small">Seed: {data.seed}</span>
          )}
        </div>

        <div className="hud-divider" />

        {description && (
          <div style={{ marginTop: '0.5rem' }}>
            <div className="text-muted" style={{ fontSize: '0.6rem', marginBottom: '0.25rem' }}>
              Описание
            </div>
            <div style={{ fontSize: '0.7rem', lineHeight: 1.5, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>
              {description}
            </div>
          </div>
        )}

        {/* Агенты */}
        {agents.length > 0 && (
          <div style={{ marginTop: '0.5rem' }}>
            <div className="text-muted" style={{ fontSize: '0.6rem', marginBottom: '0.35rem' }}>
              Агенты ({agents.length})
            </div>
            <table style={{
              width: '100%',
              fontSize: '0.65rem',
              borderCollapse: 'collapse',
            }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th style={{ textAlign: 'left', padding: '0.2rem 0.3rem', fontWeight: 500, color: 'var(--text-secondary)' }}>Имя</th>
                  <th style={{ textAlign: 'left', padding: '0.2rem 0.3rem', fontWeight: 500, color: 'var(--text-secondary)' }}>Роль</th>
                  <th style={{ textAlign: 'right', padding: '0.2rem 0.3rem', fontWeight: 500, color: 'var(--text-secondary)' }}>Реп.</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((agent) => (
                  <tr key={agent.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '0.2rem 0.3rem' }}>
                      {agent.name || agent.id}
                    </td>
                    <td style={{ padding: '0.2rem 0.3rem', color: 'var(--text-secondary)' }}>
                      {agent.position || agent.role || '—'}
                    </td>
                    <td style={{ padding: '0.2rem 0.3rem', textAlign: 'right' }}>
                      {typeof agent.initial_reputation === 'number'
                        ? agent.initial_reputation.toFixed(1)
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Нарративный контекст */}
        {data.narrative_context && data.narrative_context !== description && (
          <div style={{ marginTop: '0.5rem' }}>
            <div className="hud-divider" />
            <div className="text-muted" style={{ fontSize: '0.6rem', marginBottom: '0.25rem', marginTop: '0.5rem' }}>
              Нарративный контекст
            </div>
            <div style={{
              fontSize: '0.7rem',
              lineHeight: 1.5,
              color: 'var(--text-secondary)',
              whiteSpace: 'pre-wrap',
            }}>
              {data.narrative_context}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
