import type { SimEvent } from '../types'

interface Props {
  events: SimEvent[]
  currentRound: number
  focusRound: number | null
  onRoundClick: (round: number) => void
}

export function RoundScrubber({ events, currentRound, focusRound, onRoundClick }: Props) {
  const rounds = [...new Set(events.map((e) => e.round))].sort((a, b) => a - b)

  if (rounds.length === 0) {
    return (
      <div className="round-scrubber">
        <div className="round-scrubber-empty">
          <span className="text-muted">Раунды появятся при запуске прогона</span>
        </div>
      </div>
    )
  }

  return (
    <div className="round-scrubber">
      <div className="round-scrubber-label">Раунды</div>
      <div className="round-scrubber-track">
        {rounds.map((r) => {
          const count = events.filter((e) => e.round === r).length
          const isActive = r === focusRound
          const isCurrent = r === currentRound
          return (
            <button
              key={r}
              className={`round-scrubber-item${isActive ? ' active' : ''}${isCurrent ? ' current' : ''}`}
              onClick={() => onRoundClick(r)}
              title={`Раунд ${r} — ${count} событий`}
            >
              <span className="round-scrubber-num">{r}</span>
              <span className="round-scrubber-count">{count}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
