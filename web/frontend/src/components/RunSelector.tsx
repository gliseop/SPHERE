import { useCallback, useEffect, useState } from 'react'
import type { RunInfo } from '../types'
import { apiClient } from '../utils/apiClient'
import type { AuthUser } from '../hooks/useAuth'

interface Props {
  onPlayback: (run: RunInfo, speed: number) => void
  onLive: () => void
  speed: number
  onSpeedChange: (s: number) => void
  mode: string
  user: AuthUser | null
}

const PAGE_SIZE = 20

export function RunSelector({ onPlayback, onLive, speed, onSpeedChange, mode, user }: Props) {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [selected, setSelected] = useState<RunInfo | null>(null)
  const [showLauncher, setShowLauncher] = useState(false)
  const [launchScenario, setLaunchScenario] = useState('S1')
  const [launchGovernance, setLaunchGovernance] = useState('G1')
  const [launchSeed, setLaunchSeed] = useState('42')
  const [launchRunner, setLaunchRunner] = useState('mock')
  const [launching, setLaunching] = useState(false)
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)

  const refreshRuns = useCallback(() => {
    apiClient.get('/api/runs').then((r) => r.json()).then(setRuns).catch(console.error)
  }, [])

  useEffect(() => {
    refreshRuns()
  }, [refreshRuns])

  useEffect(() => {
    if (mode === 'idle') return
    const interval = setInterval(refreshRuns, 10_000)
    return () => clearInterval(interval)
  }, [mode, refreshRuns])

  const isIdle = mode === 'idle'

  async function handleLaunch() {
    setLaunching(true)
    try {
      const res = await apiClient.post('/api/runs/launch', {
        scenario: launchScenario,
        governance: launchGovernance,
        seed: launchSeed ? Number(launchSeed) : 42,
        runner: launchRunner,
      })
      if (res.ok) {
        setShowLauncher(false)
        refreshRuns()
      }
    } finally {
      setLaunching(false)
    }
  }

  const visibleRuns = runs.slice(0, visibleCount)
  const hasMore = runs.length > visibleCount

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0.5rem 0.875rem', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ fontSize: '0.5625rem', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.15em' }}>
          Прогоны ({runs.length})
        </div>
      </div>

      <div className="run-list" style={{ flex: '0 0 auto' }}>
        {visibleRuns.map((r) => (
          <div
            key={r.name}
            className={`run-item ${selected?.name === r.name ? 'selected' : ''}`}
            onClick={() => setSelected(r)}
          >
            <div className="run-item-name">
              {r.scenario}/{r.governance}
              {r.seed !== null ? `/s${r.seed}` : ''}
            </div>
            <div className="run-item-meta">{r.size_kb} KB</div>
          </div>
        ))}
        {hasMore && (
          <button
            className="btn-clipped small full-width"
            onClick={() => setVisibleCount((c) => c + PAGE_SIZE)}
            style={{ margin: '0.25rem 0.5rem', width: 'calc(100% - 1rem)' }}
          >
            Показать ещё ({runs.length - visibleCount})
          </button>
        )}
        {runs.length === 0 && (
          <div style={{ padding: '0.75rem 0.875rem', fontSize: '0.5625rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
            Нет прогонов
          </div>
        )}
      </div>

      <div className="speed-slider-wrap">
        <div className="speed-slider-label">
          <span>Скорость</span>
          <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{speed.toFixed(1)}x</span>
        </div>
        <input
          type="range"
          min={0.5}
          max={20}
          step={0.5}
          value={speed}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
        />
      </div>

      <div style={{ padding: '0.5rem 0.875rem', display: 'flex', flexDirection: 'column', gap: '0.5rem', borderTop: '1px solid var(--border)' }}>
        <button
          className="btn-clipped primary full-width"
          disabled={!selected || !isIdle}
          onClick={() => selected && onPlayback(selected, speed)}
        >
          ▶ Воспроизвести
        </button>

        <button
          className="btn-clipped success full-width"
          disabled={!isIdle}
          onClick={onLive}
        >
          ● Live
        </button>
      </div>

      {/* Launcher */}
      {user?.role === 'admin' && (
        <div style={{ padding: '0.5rem 0.875rem', borderTop: '1px solid var(--border)' }}>
          <button
            className="btn-clipped full-width small"
            onClick={() => setShowLauncher(!showLauncher)}
            disabled={!isIdle}
          >
            {showLauncher ? '▲ Скрыть' : '▼ Запустить новый'}
          </button>

          {showLauncher && (
            <div className="launch-form">
              <div className="launch-form-row">
                <label>Сценарий</label>
                <select className="hud-input" value={launchScenario} onChange={(e) => setLaunchScenario(e.target.value)}>
                  <option value="S0">S0 — Чистая сделка</option>
                  <option value="S1">S1 — Прямой сговор</option>
                  <option value="S2">S2 — Кумовство при найме</option>
                </select>
              </div>
              <div className="launch-form-row">
                <label>Управление</label>
                <select className="hud-input" value={launchGovernance} onChange={(e) => setLaunchGovernance(e.target.value)}>
                  <option value="G0">G0 — Без контроля</option>
                  <option value="G1">G1 — Аудитор (рекомендательный)</option>
                  <option value="G2">G2 — Аудитор с репутацией</option>
                  <option value="G3">G3 — Полный контроль (трибунал)</option>
                </select>
              </div>
              <div className="launch-form-row">
                <label>Seed</label>
                <input className="hud-input" type="number" value={launchSeed} onChange={(e) => setLaunchSeed(e.target.value)} placeholder="42" />
              </div>
              <div className="launch-form-row">
                <label>Runner</label>
                <select className="hud-input" value={launchRunner} onChange={(e) => setLaunchRunner(e.target.value)}>
                  <option value="mock">Mock</option>
                  <option value="cognitive">Cognitive</option>
                </select>
              </div>
              <button
                className="btn-clipped primary full-width small"
                onClick={handleLaunch}
                disabled={launching}
                style={{ marginTop: '0.5rem' }}
              >
                {launching ? 'Запуск...' : '▶ Запустить'}
              </button>
            </div>
          )}
        </div>
      )}

      {!isIdle && (
        <div style={{ padding: '0.375rem 0.875rem', borderTop: '1px solid var(--border)' }}>
          <div className="mode-indicator">
            <div className={`mode-dot ${mode}`} />
            <span>{mode === 'live' ? 'Live-мониторинг' : 'Воспроизведение'}</span>
          </div>
        </div>
      )}
    </div>
  )
}
