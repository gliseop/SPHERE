import { useEffect, useState } from 'react'
import type { RunInfo } from '../types'

interface Props {
  onPlayback: (run: RunInfo, speed: number) => void
  onLive: () => void
  speed: number
  onSpeedChange: (s: number) => void
  mode: string
}

export function RunSelector({ onPlayback, onLive, speed, onSpeedChange, mode }: Props) {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [selected, setSelected] = useState<RunInfo | null>(null)

  useEffect(() => {
    fetch('/api/runs')
      .then((r) => r.json())
      .then(setRuns)
      .catch(console.error)
  }, [])

  const isIdle = mode === 'idle'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '0.5rem 0.875rem', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ fontSize: '0.5625rem', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.15em' }}>
          Прогоны ({runs.length})
        </div>
      </div>

      <div className="run-list" style={{ flex: '0 0 auto' }}>
        {runs.map((r) => (
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
