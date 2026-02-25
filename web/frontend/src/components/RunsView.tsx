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

interface Props {
  onPlayback: (run: RunInfo, speed: number) => void
  onLive: () => void
  speed: number
  mode: string
  user: AuthUser | null
  activeRuns: ActiveRun[]
}

const SCENARIO_OPTIONS = [
  { value: 'S0', label: 'S0 — Чистая сделка' },
  { value: 'S1', label: 'S1 — Прямой сговор' },
  { value: 'S2', label: 'S2 — Кумовство при найме' },
]

const GOVERNANCE_OPTIONS = [
  { value: 'G0', label: 'G0 — Без контроля' },
  { value: 'G1', label: 'G1 — Аудитор (рекомендательный)' },
  { value: 'G2', label: 'G2 — Аудитор с репутацией' },
  { value: 'G3', label: 'G3 — Полный контроль (трибунал)' },
]

type FilterKey = 'all' | 'S0' | 'S1' | 'S2'

export function RunsView({ onPlayback, speed, mode, user, activeRuns }: Props) {
  const [runs, setRuns] = useState<RunInfo[] | null>(null)
  const [filter, setFilter] = useState<FilterKey>('all')

  // Launch form state
  const [launchScenario, setLaunchScenario] = useState('S1')
  const [launchGovernance, setLaunchGovernance] = useState('G1')
  const [launchSeed, setLaunchSeed] = useState('')
  const [launchRunner, setLaunchRunner] = useState('mock')
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

  const activeNames = new Set(activeRuns.filter((a) => a.status === 'running').map((a) => a.run_name))
  const hasRunning = activeNames.size > 0

  const runList = runs ?? []
  const filtered = filter === 'all'
    ? runList
    : runList.filter((r) => r.scenario === filter)

  async function handleLaunch() {
    setLaunching(true)
    try {
      const res = await apiClient.post('/api/runs/launch', {
        scenario: launchScenario,
        governance: launchGovernance,
        seed: launchSeed ? Number(launchSeed) : null,
        runner: launchRunner,
        rounds: launchRounds ? Number(launchRounds) : 25,
      })
      if (res.ok) refreshRuns()
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

  const isIdle = mode === 'idle'

  return (
    <div className="runs-view">
      <div className="runs-header">
        <span>Прогоны ({runs === null ? '…' : runList.length})</span>
        <div className="runs-filter">
          {(['all', 'S0', 'S1', 'S2'] as FilterKey[]).map((key) => (
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
              <label>Сценарий</label>
              <select className="hud-input" value={launchScenario} onChange={(e) => setLaunchScenario(e.target.value)}>
                {SCENARIO_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Управление</label>
              <select className="hud-input" value={launchGovernance} onChange={(e) => setLaunchGovernance(e.target.value)}>
                {GOVERNANCE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Seed (пусто = случайный)</label>
              <input className="hud-input" type="number" value={launchSeed} onChange={(e) => setLaunchSeed(e.target.value)} placeholder="42" />
            </div>
            <div className="form-field">
              <label>Раундов</label>
              <input className="hud-input" type="number" value={launchRounds} onChange={(e) => setLaunchRounds(e.target.value)} placeholder="25" />
            </div>
            <div className="form-field">
              <label>Runner</label>
              <select className="hud-input" value={launchRunner} onChange={(e) => setLaunchRunner(e.target.value)}>
                <option value="mock">Mock</option>
                <option value="cognitive">Cognitive</option>
              </select>
            </div>
            <div className="form-field" style={{ justifyContent: 'flex-end' }}>
              <button
                className="btn-clipped primary full-width"
                onClick={handleLaunch}
                disabled={launching || !isIdle}
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
