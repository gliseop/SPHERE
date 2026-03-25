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
  if (normalized === 'overloaded') return 'перегружена'
  if (normalized === 'recovering') return 'восстанавливается'
  if (normalized === 'stable') return 'стабильна'
  if (normalized === 'active') return 'активна'
  return status || 'неизвестно'
}

function queueMeaning(status: string, backlog: number, delay: number): string {
  if (status === 'overloaded' || backlog >= 5 || delay >= 4) {
    return 'Очередь стала bottleneck: решения, проверки или ответы накапливаются быстрее, чем система их переваривает.'
  }
  if (backlog > 0 || delay > 0) {
    return 'Есть накопление задач. Это ещё не авария, но уже влияет на ритм симуляции и может рождать жалобы, публикации и follow-up.'
  }
  return 'Очередь работает без заметного давления.'
}

function signalMeaning(signal: string): string {
  const normalized = signal.toLowerCase()
  if (normalized.includes('queue') || normalized.includes('очеред')) {
    return 'Это признак операционной перегрузки: он может усилить жалобы, медийное давление и надзор.'
  }
  if (normalized.includes('media') || normalized.includes('public')) {
    return 'Это внешний информационный сигнал: он делает происходящее более видимым и повышает репутационные ставки.'
  }
  if (normalized.includes('oversight') || normalized.includes('check')) {
    return 'Это надзорный сигнал: он увеличивает вероятность аудита, эскалации и формальных реакций.'
  }
  return 'Это активный фактор среды, который уже влияет на decisions, pressure и interpretation событий.'
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

  const climate = environment.information_climate

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
          <span className="section-label" style={{ padding: 0 }}>Что это значит</span>
        </div>
        <div className="environment-empty" style={{ textAlign: 'left', lineHeight: 1.55 }}>
          Очереди показывают, где в мире накапливается незавершённая работа.
          Сигналы показывают, какой внешний или внутренний фон уже давит на решения.
          Перегрузка означает, что система не успевает обрабатывать поток задач, жалоб или проверок.
        </div>
      </section>

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
                  <div className="environment-queue-title">{queue.title || queue.queue_id}</div>
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
                <div className="environment-empty" style={{ textAlign: 'left', padding: '0.55rem 0 0' }}>
                  {queueMeaning(queue.status, queue.backlog, queue.avg_delay_ticks)}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="environment-section">
        <div className="environment-section-header">
          <span className="section-label" style={{ padding: 0 }}>Информационный климат</span>
        </div>
        {climate ? (
          <div className="environment-queue-card">
            <div className="environment-queue-meta" style={{ marginBottom: '0.4rem' }}>
              <span>public: {climate.public_mood || '—'}</span>
              <span>oversight: {climate.oversight_attention || '—'}</span>
              <span>media: {climate.media_pressure || '—'}</span>
              <span>narrative: {climate.narrative_temperature || '—'}</span>
            </div>
            <div className="environment-empty" style={{ textAlign: 'left', padding: 0 }}>
              Этот блок показывает общий внешний фон: насколько напряжено публичное поле, насколько активен надзор и насколько “горячей” стала история.
            </div>
          </div>
        ) : (
          <div className="environment-empty">Климат среды пока не материализован</div>
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
              <div key={signal} className="environment-signal-item" title={signalMeaning(signal)}>
                <div style={{ fontWeight: 600, marginBottom: '0.15rem' }}>{signal}</div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.62rem', lineHeight: 1.45 }}>
                  {signalMeaning(signal)}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {environment.informal_links && environment.informal_links.length > 0 && (
        <section className="environment-section">
          <div className="environment-section-header">
            <span className="section-label" style={{ padding: 0 }}>Неформальные связи</span>
          </div>
          <div className="environment-queue-list">
            {environment.informal_links.slice(0, 8).map((link) => (
              <div key={link.link_id} className="environment-queue-card">
                <div className="environment-queue-header">
                  <div className="environment-queue-title">{link.agent_a_id} / {link.agent_b_id}</div>
                  <span className="badge small accent">{link.link_type || 'link'}</span>
                </div>
                <div className="environment-queue-meta">
                  <span>strength {link.strength.toFixed(2)}</span>
                  <span>{link.visibility || '—'}</span>
                  <span>{link.source || '—'}</span>
                </div>
                {link.pressure && (
                  <div className="environment-empty" style={{ textAlign: 'left', padding: '0.55rem 0 0' }}>
                    Давление: {link.pressure}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
