import type { SimEvent } from '../types'
import { toDayKey, formatDayLabel } from '../utils/time'

interface TimelinePoint {
  key: string
  label: string
  count: number
}

interface Props {
  events: SimEvent[]
  focusDay: string | null
  onDayClick: (dayKey: string) => void
}

export function Timeline({ events, focusDay, onDayClick }: Props) {
  const countByDay = new Map<string, number>()
  for (const event of events) {
    const key = toDayKey(event)
    countByDay.set(key, (countByDay.get(key) ?? 0) + 1)
  }

  const points: TimelinePoint[] = [...countByDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, count]) => ({
      key,
      label: formatDayLabel(key),
      count,
    }))

  const maxCount = points.reduce((m, p) => Math.max(m, p.count), 1)

  if (points.length === 0) {
    return (
      <div className="timeline">
        <div className="timeline-empty">
          <span className="text-muted">Временная шкала появится при запуске прогона</span>
        </div>
      </div>
    )
  }

  return (
    <div className="timeline">
      <div className="timeline-label">Время</div>
      <div className="timeline-track">
        {points.map((point) => {
          const isActive = point.key === focusDay
          const dotScale = 0.5 + (point.count / maxCount) * 0.7
          return (
            <button
              key={point.key}
              className={`timeline-item${isActive ? ' active' : ''}`}
              onClick={() => onDayClick(point.key)}
              title={`${point.label} — ${point.count} событий`}
            >
              <span className="timeline-num">{point.label}</span>
              <span
                className="timeline-dot"
                style={{ transform: `scale(${dotScale.toFixed(2)})` }}
              />
              <span className="timeline-count">{point.count}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
