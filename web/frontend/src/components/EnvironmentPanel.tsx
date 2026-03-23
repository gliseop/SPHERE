import { useMemo } from 'react'
import type { SimEnvironment } from '../types'

interface Props {
  environment: SimEnvironment
}

function queueBadgeClass(pressure: string): string {
  const normalized = pressure.trim().toLowerCase()
  if (normalized === 'critical') return 'danger'
  if (normalized === 'high') return 'warning'
  if (normalized === 'elevated') return 'accent'
  return 'success'
}

function statusLabel(status: string): string {
  const normalized = status.trim().toLowerCase()
  if (normalized === 'overloaded') return 'перегрузка'
  if (normalized === 'recovering') return 'восстановление'
  if (normalized === 'stable') return 'стабильно'
  return status || 'неизвестно'
}

export function EnvironmentPanel({ environment }: Props) {
  const orderedQueues = useMemo(() => {
    return [...environment.queues].sort((a, b) => {
      const pressureRank = (value: string) => {
        const normalized = value.trim().toLowerCase()
        if (normalized === 'critical') return 0
        if (normalized === 'high') return 1
        if (normalized === 'elevated') return 2
        return 3
      }
      return (
        pressureRank(a.pressure) - pressureRank(b.pressure)
        || b.backlog - a.backlog
        || b.avg_delay_ticks - a.avg_delay_ticks
        || a.queue_id.localeCompare(b.queue_id, 'ru')
      )
    })
  }, [environment.queues])

  const overloadedCount = useMemo(
    () => environment.queues.filter((queue) => queue.status === 'overloaded' || queue.pressure === 'critical').length,
    [environment.queues]
  )

  return (
    <div className="environment-panel">
      <div className="environment-summary-grid">
        <div className="environment-summary-card">
          <div className="environment-summary-label">Очереди</div>
          <div className="environment-summary-value">{environment.queues.length}</div>
        </div>
        <div className="environment-summary-card">
          <div className="environment-summary-label">Перегрузка</div>
          <div className={`environment-summary-value${overloadedCount > 0 ? ' danger' : ''}`}>{overloadedCount}</div>
        </div>
        <div className="environment-summary-card">
          <div className="environment-summary-label">Сигналы</div>
          <div className={`environment-summary-value${environment.active_signals.length > 0 ? ' accent' : ''}`}>
            {environment.active_signals.length}
          </div>
        </div>
      </div>

      <section className="environment-section">
        <div className="environment-section-header">
          <span className="section-label" style={{ padding: 0 }}>Операционные очереди</span>
        </div>
        {orderedQueues.length === 0 ? (
          <div className="environment-empty">Нет активных очередей</div>
        ) : (
          <div className="environment-queue-list">
            {orderedQueues.map((queue) => (
              <div key={queue.queue_id} className="environment-queue-card">
                <div className="environment-queue-header">
                  <div className="environment-queue-title">{queue.queue_id}</div>
                  <span className={`badge small ${queueBadgeClass(queue.pressure)}`}>{queue.pressure || 'low'}</span>
                </div>
                <div className="environment-queue-meta">
                  <span>{statusLabel(queue.status)}</span>
                  {queue.owner_org_id && <span>{queue.owner_org_id}</span>}
                  {queue.zone_id && <span>{queue.zone_id}</span>}
                </div>
                <div className="environment-queue-stats">
                  <div>
                    <span className="environment-stat-name">backlog</span>
                    <span className="environment-stat-value">{queue.backlog}</span>
                  </div>
                  <div>
                    <span className="environment-stat-name">capacity</span>
                    <span className="environment-stat-value">{queue.capacity_per_tick}</span>
                  </div>
                  <div>
                    <span className="environment-stat-name">delay</span>
                    <span className="environment-stat-value">{queue.avg_delay_ticks}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="environment-section">
        <div className="environment-section-header">
          <span className="section-label" style={{ padding: 0 }}>Активные сигналы</span>
        </div>
        {environment.active_signals.length === 0 ? (
          <div className="environment-empty">Сигналы отсутствуют</div>
        ) : (
          <div className="environment-signal-list">
            {environment.active_signals.map((signal) => (
              <div key={signal} className="environment-signal-item">
                {signal}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
