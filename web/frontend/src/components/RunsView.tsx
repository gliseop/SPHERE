import { useCallback, useEffect, useState } from 'react'
import type { RunInfo } from '../types'
import { apiClient } from '../utils/apiClient'
import type { AuthUser } from '../hooks/useAuth'

interface ActiveRun {
  run_name: string
  pid: number
  status: 'running' | 'finished'
  returncode?: number
}

interface SavedScenario {
  id?: string
  name: string
  scenario: string
  governance: string
  rounds: number
  seed: number | null
  sim_config?: Record<string, unknown> | null
}

interface Props {
  onPlayback: (run: RunInfo, speed: number) => void
  onLive: (runName?: string) => void
  speed: number
  mode: string
  user: AuthUser | null
  activeRuns: ActiveRun[]
}

interface TemplateScenario {
  id: string
  title: string
  description?: string
}

interface GovernanceModeItem {
  id: string
  label: string
  description?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

const FALLBACK_SCENARIOS: TemplateScenario[] = [
  { id: 'S0', title: 'Чистая сделка' },
  { id: 'S1', title: 'Прямой сговор' },
  { id: 'S2', title: 'Кумовство при найме' },
]

const FALLBACK_GOVERNANCE: GovernanceModeItem[] = [
  { id: 'G0', label: 'G0 — Без контроля' },
  { id: 'G1', label: 'G1 — Аудитор (рекомендательный)' },
  { id: 'G2', label: 'G2 — Аудитор (санкции по репутации)' },
  { id: 'G3', label: 'G3 — Полный контроль (трибунал)' },
]

export function RunsView({ onPlayback, onLive, speed, mode, user, activeRuns }: Props) {
  const [runs, setRuns] = useState<RunInfo[] | null>(null)
  const [filter, setFilter] = useState<string>('all')
  const [templateScenarios, setTemplateScenarios] = useState<TemplateScenario[] | null>(null)
  const [governanceModes, setGovernanceModes] = useState<GovernanceModeItem[] | null>(null)

  // Launch form state
  const [launchSource, setLaunchSource] = useState<'template' | 'saved'>('template')
  const [savedScenarios, setSavedScenarios] = useState<SavedScenario[] | null>(null)
  const [launchScenarioId, setLaunchScenarioId] = useState('')
  const [launchScenario, setLaunchScenario] = useState('S1')
  const [launchGovernance, setLaunchGovernance] = useState('G1')
  const [launchSeed, setLaunchSeed] = useState('')
  const [launchRounds, setLaunchRounds] = useState('25')
  const [launching, setLaunching] = useState(false)
  const [stopping, setStopping] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)

  const refreshRuns = useCallback(() => {
    apiClient.get('/api/runs')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setRuns(Array.isArray(data) ? data : []))
      .catch(() => setRuns([]))
  }, [])

  useEffect(() => {
    refreshRuns()
    const interval = setInterval(refreshRuns, 5_000)
    return () => clearInterval(interval)
  }, [refreshRuns])

  useEffect(() => {
    apiClient.get('/api/templates/scenarios')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => {
        if (Array.isArray(data)) {
          setTemplateScenarios(
            data
              .map((x): TemplateScenario | null => {
                if (!isRecord(x)) return null
                const idRaw = x.id
                const id = typeof idRaw === 'string' ? idRaw : String(idRaw ?? '')
                if (!id) return null
                const titleRaw = x.title
                const title = typeof titleRaw === 'string' ? titleRaw : id
                const description = typeof x.description === 'string' ? x.description : undefined
                return description ? { id, title, description } : { id, title }
              })
              .filter((x): x is TemplateScenario => x !== null),
          )
          return
        }
        setTemplateScenarios([])
      })
      .catch(() => setTemplateScenarios([]))

    apiClient.get('/api/templates/governance')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => {
        if (Array.isArray(data)) {
          setGovernanceModes(
            data
              .map((x): GovernanceModeItem | null => {
                if (!isRecord(x)) return null
                const idRaw = x.id
                const id = typeof idRaw === 'string' ? idRaw : String(idRaw ?? '')
                if (!id) return null
                const labelRaw = x.label
                const label = typeof labelRaw === 'string' ? labelRaw : id
                const description = typeof x.description === 'string' ? x.description : undefined
                return description ? { id, label, description } : { id, label }
              })
              .filter((x): x is GovernanceModeItem => x !== null),
          )
          return
        }
        setGovernanceModes([])
      })
      .catch(() => setGovernanceModes([]))
  }, [])

  useEffect(() => {
    if (user?.role !== 'admin') return
    apiClient.get('/api/scenarios')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setSavedScenarios(Array.isArray(data) ? data : []))
      .catch(() => setSavedScenarios([]))
  }, [user?.role])

  const activeNames = new Set(activeRuns.filter((a) => a.status === 'running').map((a) => a.run_name))
  const hasRunning = activeNames.size > 0

  const runList = runs ?? []
  const filtered = filter === 'all'
    ? runList
    : runList.filter((r) => r.scenario === filter)
  const scenarioOptions = templateScenarios ?? FALLBACK_SCENARIOS
  const governanceOptions = governanceModes ?? FALLBACK_GOVERNANCE
  const scenarioChips = ['all', ...scenarioOptions.map((s) => s.id)]

  async function handleLaunch() {
    setLaunching(true)
    try {
      if (launchSource === 'saved') {
        if (!launchScenarioId) return
      }

      const res = launchSource === 'saved'
        ? await apiClient.post(`/api/scenarios/${launchScenarioId}/run`)
        : await apiClient.post('/api/runs/launch', {
          scenario: launchScenario,
          governance: launchGovernance,
          seed: launchSeed ? Number(launchSeed) : null,
          runner: 'cognitive',
          rounds: launchRounds ? Number(launchRounds) : 25,
        })
      if (!res.ok) return
      const data = await res.json().catch(() => null) as { run_name?: string } | null
      refreshRuns()
      if (data?.run_name) {
        onLive(data.run_name)
      }
    } finally {
      setLaunching(false)
    }
  }

  async function handleStop(runName: string) {
    setStopping(runName)
    try {
      await apiClient.post(`/api/runs/${runName}/stop`)
      refreshRuns()
    } finally {
      setStopping(null)
    }
  }

  async function handleDelete(runName: string) {
    if (!window.confirm(`Удалить прогон ${runName}? Это действие необратимо.`)) return
    setDeleting(runName)
    try {
      const res = await apiClient.delete(`/api/runs/${runName}`)
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        window.alert(text || 'Не удалось удалить прогон')
        return
      }
      refreshRuns()
    } finally {
      setDeleting(null)
    }
  }

  const selectedSaved = launchSource === 'saved'
    ? (savedScenarios ?? []).find((s) => s.id === launchScenarioId) ?? null
    : null

  const isIdle = mode === 'idle'

  return (
    <div className="runs-view">
      <div className="runs-header">
        <span>Прогоны ({runs === null ? '…' : runList.length})</span>
        <div className="runs-filter">
          {scenarioChips.map((key) => (
            <button
              key={key}
              className={`runs-filter-chip${filter === key ? ' active' : ''}`}
              onClick={() => setFilter(key)}
            >
              {key === 'all' ? 'Все' : key}
            </button>
          ))}
        </div>
      </div>

      {/* Launch panel — admin only */}
      {user?.role === 'admin' && (
        <div className="runs-launch-panel hud-panel compact">
          <div className="corner tl accent" /><div className="corner br" />
          <div className="runs-launch-title">Запуск нового прогона</div>
          <div className="runs-launch-grid">
            <div className="form-field">
              <label>Источник</label>
              <select
                className="hud-input"
                value={launchSource}
                onChange={(e) => setLaunchSource(e.target.value as 'template' | 'saved')}
              >
                <option value="template">Шаблон (S/G)</option>
                <option value="saved">Сценарий (из библиотеки)</option>
              </select>
            </div>

            {launchSource === 'template' ? (
              <>
                <div className="form-field">
                  <label>Сценарий</label>
                  <select className="hud-input" value={launchScenario} onChange={(e) => setLaunchScenario(e.target.value)}>
                    {scenarioOptions.map((o) => (
                      <option key={o.id} value={o.id} title={o.description || ''}>
                        {o.id} — {o.title}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label>Управление</label>
                  <select className="hud-input" value={launchGovernance} onChange={(e) => setLaunchGovernance(e.target.value)}>
                    {governanceOptions.map((o) => (
                      <option key={o.id} value={o.id} title={o.description || ''}>{o.label}</option>
                    ))}
                  </select>
                </div>
                <div className="form-field">
                  <label>Seed (пусто = случайный)</label>
                  <input className="hud-input" type="number" value={launchSeed} onChange={(e) => setLaunchSeed(e.target.value)} placeholder="42" />
                </div>
                <div className="form-field">
                  <label title="Количество шагов симуляции (раундов)">Шагов</label>
                  <input className="hud-input" type="number" value={launchRounds} onChange={(e) => setLaunchRounds(e.target.value)} placeholder="25" />
                </div>
              </>
            ) : (
              <>
                <div className="form-field" style={{ gridColumn: 'span 2' }}>
                  <label>Сценарий</label>
                  <select className="hud-input" value={launchScenarioId} onChange={(e) => setLaunchScenarioId(e.target.value)}>
                    <option value="">— выбрать —</option>
                    {(savedScenarios ?? []).map((s) => (
                      <option key={s.id ?? s.name} value={s.id ?? ''} disabled={!s.id}>
                        {s.name} ({s.scenario}/{s.governance})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="form-field" style={{ gridColumn: 'span 3' }}>
                  <label>Параметры</label>
                  <div className="text-muted" style={{ fontSize: '0.7rem' }}>
                    {selectedSaved
                      ? `${selectedSaved.scenario} / ${selectedSaved.governance} / шагов: ${selectedSaved.rounds}`
                        + (selectedSaved.seed !== null ? ` / seed ${selectedSaved.seed}` : '')
                        + (selectedSaved.sim_config ? ' / custom' : '')
                      : 'Выберите сценарий из библиотеки'}
                  </div>
                </div>
              </>
            )}

            <div className="form-field" style={{ justifyContent: 'flex-end', gridColumn: 'span 3' }}>
              <button
                className="btn-clipped primary full-width"
                onClick={handleLaunch}
                disabled={launching || !isIdle || (launchSource === 'saved' && !launchScenarioId)}
              >
                {launching ? 'Запуск...' : '▶ Запустить'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Active runs section */}
      {activeRuns.length > 0 && (
        <div className="runs-active-section">
          <div className="runs-section-label">Активные ({activeRuns.filter((a) => a.status === 'running').length})</div>
          <div className="runs-active-list">
            {activeRuns.filter((a) => a.status === 'running').map((a) => (
              <div key={a.run_name} className="runs-active-item">
                <div className="active-dot" />
                <span className="runs-active-name">{a.run_name}</span>
                <span className="runs-active-pid">PID {a.pid}</span>
                <button
                  className="btn-clipped success small"
                  onClick={() => onLive(a.run_name)}
                  title="Перейти в монитор (Live)"
                >
                  ● Live
                </button>
                {user?.role === 'admin' && (
                  <button
                    className="btn-clipped danger small"
                    onClick={() => handleStop(a.run_name)}
                    disabled={stopping === a.run_name}
                  >
                    {stopping === a.run_name ? '...' : 'Стоп'}
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Runs table */}
      <div className="runs-table-wrap">
        <table className="runs-table">
          <thead>
            <tr>
              {hasRunning && <th></th>}
              <th>Имя</th>
              <th>Сценарий</th>
              <th>Управление</th>
              <th>Seed</th>
              <th>Размер</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => {
              const isActive = activeNames.has(r.name)
              return (
                <tr key={r.name} className={`runs-row${isActive ? ' active' : ''}`}>
                  {hasRunning && (
                    <td className="runs-status-cell">
                      {isActive && <div className="active-dot" />}
                    </td>
                  )}
                  <td className="runs-name-cell">{r.name}</td>
                  <td><span className="badge small accent">{r.scenario || '—'}</span></td>
                  <td><span className="badge small info">{r.governance || '—'}</span></td>
                  <td className="runs-seed-cell">{r.seed ?? '—'}</td>
                  <td className="runs-size-cell">{r.size_kb} KB</td>
                  <td className="runs-actions-cell">
                    <button
                      className="btn-clipped primary small"
                      disabled={!isIdle}
                      onClick={() => onPlayback(r, speed)}
                      title="Воспроизвести"
                    >
                      ▶
                    </button>
                    {isActive && (
                      <button
                        className="btn-clipped success small"
                        onClick={() => onLive(r.name)}
                        title="Перейти в монитор (Live)"
                      >
                        ●
                      </button>
                    )}
                    {isActive && user?.role === 'admin' && (
                      <button
                        className="btn-clipped danger small"
                        onClick={() => handleStop(r.name)}
                        disabled={stopping === r.name}
                        title="Остановить"
                      >
                        ■
                      </button>
                    )}
                    {user?.role === 'admin' && (
                      <button
                        className="btn-clipped danger small"
                        onClick={() => handleDelete(r.name)}
                        disabled={Boolean(isActive) || deleting === r.name || !isIdle}
                        title={isActive ? 'Нельзя удалить активный прогон' : 'Удалить'}
                        style={{ marginLeft: '0.35rem' }}
                      >
                        🗑
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <div className="runs-empty">
            {runs === null
              ? 'Загрузка…'
              : (filter === 'all' ? 'Нет прогонов' : `Нет прогонов для ${filter}`)}
          </div>
        )}
      </div>
    </div>
  )
}
