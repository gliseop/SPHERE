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

  return (
    <div className="p-2 space-y-3 text-sm">
      <div>
        <div className="text-slate-400 text-xs mb-1">Прогоны ({runs.length})</div>
        <div className="space-y-0.5 max-h-64 overflow-y-auto">
          {runs.map((r) => (
            <button
              key={r.name}
              onClick={() => setSelected(r)}
              className={`w-full text-left px-2 py-1 rounded text-xs font-mono truncate
                ${selected?.name === r.name
                  ? 'bg-blue-800 text-white'
                  : 'text-slate-300 hover:bg-slate-800'
                }`}
            >
              {r.scenario}/{r.governance}
              {r.seed !== null ? `/seed${r.seed}` : ''}
              <span className="text-slate-500 ml-1">({r.size_kb}KB)</span>
            </button>
          ))}
        </div>
      </div>

      <div>
        <div className="text-slate-400 text-xs mb-1">
          Скорость: {speed.toFixed(1)}x
        </div>
        <input
          type="range"
          min={0.5}
          max={20}
          step={0.5}
          value={speed}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
          className="w-full accent-blue-500"
        />
      </div>

      <button
        disabled={!selected || mode !== 'idle'}
        onClick={() => selected && onPlayback(selected, speed)}
        className="w-full py-1.5 rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-40
                   text-white text-xs font-medium"
      >
        ▶ Воспроизвести
      </button>

      <button
        disabled={mode !== 'idle'}
        onClick={onLive}
        className="w-full py-1.5 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40
                   text-white text-xs font-medium"
      >
        ● Live-мониторинг
      </button>

      {mode !== 'idle' && (
        <div className="text-xs text-center text-emerald-400">
          {mode === 'live' ? 'Live...' : 'Воспроизведение...'}
        </div>
      )}
    </div>
  )
}
