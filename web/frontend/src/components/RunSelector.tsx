import { useCallback, useEffect, useState } from 'react'
import type { RunInfo } from '../types'
import { apiClient } from '../utils/apiClient'
import type { AuthUser } from '../hooks/useAuth'
import { Icon } from './Icons'

interface Props {
  onPlayback: (run: RunInfo, speed: number) => void
  onLive: (runName?: string) => void
  onOpenRun: (runName: string) => void
  speed: number
  onSpeedChange: (s: number) => void
  mode: string
  user: AuthUser | null
  activeRuns: string[]
  onGoToRuns: () => void
}

export function RunSelector({ onPlayback, onLive, onOpenRun, speed, onSpeedChange, mode, activeRuns, onGoToRuns }: Props) {
  const [runs, setRuns] = useState<RunInfo[] | null>(null)
  const [selected, setSelected] = useState<RunInfo | null>(null)

  const activeSet = new Set(activeRuns)
  const hasRunning = activeRuns.length > 0

  const refreshRuns = useCallback(() => {
    apiClient.get('/api/runs')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setRuns(Array.isArray(data) ? data : []))
      .catch(() => setRuns([]))
  }, [])

  useEffect(() => {
    refreshRuns()
  }, [refreshRuns])

  useEffect(() => {
    if (mode === 'idle') return
    const interval = setInterval(refreshRuns, 10_000)
    return () => clearInterval(interval)
  }, [mode, refreshRuns])

  const isIdle = mode === 'idle' || mode === 'snapshot'

  const allRuns = runs ?? []
  const visibleRuns = allRuns.slice(0, 3)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0.5rem 0.875rem', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ fontSize: '0.5625rem', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.15em' }}>
          Прогоны ({runs === null ? '…' : allRuns.length})
        </div>
      </div>

      <div className="run-list" style={{ flex: '0 0 auto' }}>
        {visibleRuns.map((r) => {
          const isActive = activeSet.has(r.name)
          const showScenario = Boolean(r.scenario)
          const showGov = Boolean(r.governance)
          return (
            <div
              key={r.name}
              className={`run-item${selected?.name === r.name ? ' selected' : ''}${isActive ? ' running' : ''}`}
              onClick={() => setSelected(r)}
              onDoubleClick={() => onOpenRun(r.name)}
              title={r.display_name || r.scenario_title || r.name}
            >
              <div className="run-item-name">
                {isActive && <span className="active-dot-inline" />}
                {r.display_name || r.scenario_title || (showScenario ? r.scenario : r.name)}
              </div>
              <div className="run-item-meta">
                {r.simulated_start_date && r.simulated_end_date
                  ? `${r.simulated_start_date} - ${r.simulated_end_date}`
                  : r.created_at
                    ? new Date(r.created_at * 1000).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
                    : ''}
                {(r.simulated_start_date || r.created_at) ? ' · ' : ''}
                {showGov ? (r.governance_label || r.governance) : ''}
                {showGov ? ' · ' : ''}
                {r.size_kb} KB
              </div>
            </div>
          )
        })}
        {allRuns.length === 0 && (
          <div style={{ padding: '0.75rem 0.875rem', fontSize: '0.5625rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
            {runs === null ? 'Загрузка…' : 'Нет прогонов'}
          </div>
        )}
      </div>

      <div className="speed-slider-wrap">
        <div className="speed-slider-label">
          <span>Скорость</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{speed.toFixed(1)}x</span>
            <button
              className="btn-clipped primary small"
              disabled={!selected || !isIdle}
              onClick={() => selected && onPlayback(selected, speed)}
              title="Воспроизвести"
            >
              <Icon name="play" size={14} />
            </button>
          </div>
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
        {hasRunning && (
          <button
            className="btn-clipped success full-width"
            onClick={() => {
              const target = selected && activeSet.has(selected.name)
                ? selected.name
                : activeRuns[activeRuns.length - 1]
              onLive(target)
            }}
            title={selected && activeSet.has(selected.name)
              ? 'Live по выбранному активному прогону'
              : 'Live по самому свежему активному прогону'}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.45rem' }}>
              <Icon name="live" size={14} />
              <span>Live</span>
            </span>
          </button>
        )}
      </div>

      <div style={{ padding: '0.375rem 0.875rem', borderTop: '1px solid var(--border)' }}>
        <button
          className="btn-clipped small full-width"
          onClick={onGoToRuns}
        >
          Все прогоны
        </button>
      </div>

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
