import { useMemo } from 'react'
import type { SimEnvironment } from '../types'

interface Props {
  environment: SimEnvironment
}

function signalMeaning(signal: string): string {
  if (signal.trim()) {
    return 'Это активный фактор среды: его смысл задаётся самим сценарием и текущим ходом мира, а не жёсткой словарной рубрикой UI.'
  }
  return 'Это активный фактор среды, который уже влияет на decisions, pressure и interpretation событий.'
}

export function EnvironmentPanel({ environment }: Props) {
  const informalLinkCount = useMemo(
    () => environment.informal_links?.length ?? 0,
    [environment.informal_links]
  )

  const climate = environment.information_climate

  return (
    <div className="environment-panel">
      <div className="environment-summary-grid">
        <div className="environment-summary-card">
          <div className="environment-summary-label">Сигналы</div>
          <div className={`environment-summary-value${environment.active_signals.length > 0 ? ' accent' : ''}`}>
            {environment.active_signals.length}
          </div>
        </div>
        <div className="environment-summary-card">
          <div className="environment-summary-label">Связи</div>
          <div className={`environment-summary-value${informalLinkCount > 0 ? ' accent' : ''}`}>{informalLinkCount}</div>
        </div>
        <div className="environment-summary-card">
          <div className="environment-summary-label">Климат</div>
          <div className={`environment-summary-value${climate ? ' accent' : ''}`}>{climate ? 'on' : 'off'}</div>
        </div>
      </div>

      <section className="environment-section">
        <div className="environment-section-header">
          <span className="section-label" style={{ padding: 0 }}>Что это значит</span>
        </div>
        <div className="environment-empty" style={{ textAlign: 'left', lineHeight: 1.55 }}>
          Этот блок больше не показывает искусственно материализованные очереди.
          Здесь остаётся только то, что действительно описывает фон сцены: информационный климат,
          активные сигналы и неформальные связи между участниками.
        </div>
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
