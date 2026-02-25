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
  activeRuns: string[]
  onGoToRuns: () => void
}

const PAGE_SIZE = 20

export function RunSelector({ onPlayback, onLive, speed, onSpeedChange, mode, activeRuns, onGoToRuns }: Props) {
  const [runs, setRuns] = useState<RunInfo[] | null>(null)
  const [selected, setSelected] = useState<RunInfo | null>(null)
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)

  const activeSet = new Set(activeRuns)

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

  const isIdle = mode === 'idle'

  const allRuns = runs ?? []
  const visibleRuns = allRuns.slice(0, visibleCount)
  const hasMore = allRuns.length > visibleCount

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
            >
              <div className="run-item-name">
                {isActive && <span className="active-dot-inline" />}
                {showScenario ? r.scenario : r.name}
                {showGov ? `/${r.governance}` : ''}
                {r.seed !== null ? `/s${r.seed}` : ''}
                {r.variant && (
                  <span className="badge small info" style={{ marginLeft: '0.35rem' }}>
                    {r.variant}
                  </span>
                )}
              </div>
              <div className="run-item-meta">
                {r.size_kb} KB
                {isActive && <span className="badge small success" style={{ marginLeft: '0.35rem' }}>running</span>}
              </div>
            </div>
          )
        })}
        {hasMore && (
          <button
            className="btn-clipped small full-width"
            onClick={() => setVisibleCount((c) => c + PAGE_SIZE)}
            style={{ margin: '0.25rem 0.5rem', width: 'calc(100% - 1rem)' }}
          >
            Показать ещё ({allRuns.length - visibleCount})
          </button>
        )}
        {allRuns.length === 0 && (
          <div style={{ padding: '0.75rem 0.875rem', fontSize: '0.5625rem', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
            {runs === null ? 'Загрузка…' : 'Нет прогонов'}
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

      <div style={{ padding: '0.375rem 0.875rem', borderTop: '1px solid var(--border)' }}>
        <button
          className="btn-clipped small full-width"
          onClick={onGoToRuns}
        >
          Все прогоны →
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
