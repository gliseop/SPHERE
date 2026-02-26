import { useEffect, useRef, useState } from 'react'
import { apiClient } from '../utils/apiClient'
import type { AuthUser } from '../hooks/useAuth'

interface Agent {
  id: string
  name: string
  role: 'official' | 'business' | 'auditor'
  initial_reputation: number
}

interface Scenario {
  id?: string
  name: string
  description?: string
  scenario: string
  governance: string
  rounds: number
  seed: number | null
  agents: Agent[]
  sim_config?: Record<string, unknown> | null
  runner?: string
}

const EMPTY_SCENARIO: Scenario = {
  name: '',
  description: '',
  scenario: 'S1',
  governance: 'G1',
  rounds: 10,
  seed: null,
  agents: [],
  runner: 'cognitive',
}

const ROLE_OPTIONS = [
  { value: 'official', label: 'Чиновник' },
  { value: 'business', label: 'Подрядчик' },
  { value: 'auditor',  label: 'Аудитор' },
]

const SCENARIO_OPTIONS = [
  { value: 'S0', label: 'S0 — Чистая сделка' },
  { value: 'S1', label: 'S1 — Прямой сговор' },
  { value: 'S2', label: 'S2 — Кумовство при найме' },
]

const GOVERNANCE_OPTIONS = [
  { value: 'G0', label: 'G0 — Без контроля' },
  { value: 'G1', label: 'G1 — Аудитор (рекомендательный)' },
  { value: 'G2', label: 'G2 — Аудитор (санкции по репутации)' },
  { value: 'G3', label: 'G3 — Полный контроль (трибунал)' },
]

export function ScenariosView({ onLaunch, onGoLive, user }: {
  onLaunch?: () => void
  onGoLive?: (runName?: string) => void
  user: AuthUser | null
}) {
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null)
  const [editing, setEditing] = useState<Scenario | null>(null)
  const [showJson, setShowJson] = useState(false)
  const [saving, setSaving] = useState(false)
  const [simConfigText, setSimConfigText] = useState('')
  const [simConfigError, setSimConfigError] = useState<string | null>(null)
  const [simConfigLoading, setSimConfigLoading] = useState(false)
  const prevEditingRef = useRef<Scenario | null>(null)

  useEffect(() => {
    if (editing && prevEditingRef.current === null) {
      setSimConfigText(editing.sim_config ? JSON.stringify(editing.sim_config, null, 2) : '')
      setSimConfigError(null)
    }
    if (!editing && prevEditingRef.current !== null) {
      setSimConfigText('')
      setSimConfigError(null)
    }
    prevEditingRef.current = editing
  }, [editing])

  useEffect(() => {
    apiClient.get('/api/scenarios')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setScenarios(Array.isArray(data) ? data : []))
      .catch(() => setScenarios([]))
  }, [])

  async function handleSave() {
    if (!editing) return
    let simConfig: Record<string, unknown> | undefined
    if (simConfigText.trim()) {
      try {
        const parsed = JSON.parse(simConfigText) as unknown
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          setSimConfigError('Ожидается JSON-объект (ScenarioConfig)')
          return
        }
        simConfig = parsed as Record<string, unknown>
      } catch (e) {
        setSimConfigError(e instanceof Error ? e.message : 'Некорректный JSON')
        return
      }
    }

    setSaving(true)
    try {
      const payload: Scenario = {
        ...editing,
        runner: 'cognitive',
        sim_config: simConfig,
      }
      const res = editing.id
        ? await apiClient.put(`/api/scenarios/${editing.id}`, payload)
        : await apiClient.post('/api/scenarios', payload)
      const saved: Scenario = await res.json()
      setScenarios((prev) => {
        const list = Array.isArray(prev) ? prev : []
        return editing.id
          ? list.map((s) => (s.id === saved.id ? saved : s))
          : [...list, saved]
      })
      setEditing(null)
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm('Удалить сценарий? Это действие необратимо.')) return
    await apiClient.delete(`/api/scenarios/${id}`)
    setScenarios((prev) => (Array.isArray(prev) ? prev.filter((s) => s.id !== id) : []))
  }

  async function handleRun(s: Scenario) {
    try {
      const res = s.id
        ? await apiClient.post(`/api/scenarios/${s.id}/run`)
        : await apiClient.post('/api/runs/launch', {
          scenario: s.scenario,
          governance: s.governance,
          seed: s.seed,
          runner: 'cognitive',
          rounds: s.rounds,
        })
      if (res.ok) {
        const data = await res.json().catch(() => null) as { run_name?: string } | null
        onLaunch?.()
        if (data?.run_name) onGoLive?.(data.run_name)
      }
    } catch {
      // Ошибка сети — молча обрабатываем
    }
  }

  function addAgent() {
    if (!editing) return
    const idx = editing.agents.length + 1
    setEditing({
      ...editing,
      agents: [...editing.agents, {
        id: `agent_${idx}`,
        name: `Агент ${idx}`,
        role: 'official',
        initial_reputation: 7.0,
      }],
    })
  }

  function updateAgent(i: number, field: keyof Agent, value: string | number) {
    if (!editing) return
    const agents = editing.agents.map((a, idx) =>
      idx === i ? { ...a, [field]: value } : a
    )
    if (field === 'name') {
      const prefix = editing.agents[i].role === 'official' ? 'off' :
                     editing.agents[i].role === 'business' ? 'biz' : 'aud'
      agents[i].id = `${prefix}_${String(value).toLowerCase().replace(/\s+/g, '_').slice(0, 12)}`
    }
    if (field === 'role') {
      const prefix = value === 'official' ? 'off' : value === 'business' ? 'biz' : 'aud'
      agents[i].id = `${prefix}_${editing.agents[i].name.toLowerCase().replace(/\s+/g, '_').slice(0, 12)}`
      if (value === 'auditor') {
        agents[i].initial_reputation = 0
      }
    }
    setEditing({ ...editing, agents })
  }

  function removeAgent(i: number) {
    if (!editing) return
    setEditing({ ...editing, agents: editing.agents.filter((_, idx) => idx !== i) })
  }

  const scenarioList = scenarios ?? []

  if (editing) {
    return (
      <div className="scenarios-editor">
        <div className="scenarios-editor-header">
          <span>{editing.id ? 'Редактировать сценарий' : 'Новый сценарий'}</span>
          <button className="btn-clipped small" onClick={() => setEditing(null)}>✕ Отмена</button>
        </div>

        <div className="scenarios-form">
          <div className="form-field">
            <label>Название</label>
            <input
              className="hud-input"
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              placeholder="Название сценария"
            />
          </div>

          <div className="form-field">
            <label>Описание</label>
            <textarea
              className="hud-input"
              rows={2}
              value={editing.description ?? ''}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              placeholder="Краткое описание"
            />
          </div>

          <div className="form-row">
            <div className="form-field">
              <label>Шаблон сценария</label>
              <select
                className="hud-input"
                value={editing.scenario}
                onChange={(e) => setEditing({ ...editing, scenario: e.target.value })}
              >
                {SCENARIO_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div className="form-field">
              <label>Режим управления</label>
              <select
                className="hud-input"
                value={editing.governance}
                onChange={(e) => setEditing({ ...editing, governance: e.target.value })}
              >
                {GOVERNANCE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="form-row">
            <div className="form-field">
              <label title="Количество шагов симуляции (раундов)">Шагов</label>
              <input
                className="hud-input"
                type="number"
                min={1} max={100}
                value={editing.rounds}
                onChange={(e) => setEditing({ ...editing, rounds: Number(e.target.value) })}
              />
            </div>
            <div className="form-field">
              <label>Seed (пусто = случайный)</label>
              <input
                className="hud-input"
                type="number"
                value={editing.seed ?? ''}
                onChange={(e) => setEditing({ ...editing, seed: e.target.value ? Number(e.target.value) : null })}
                placeholder="случайный"
              />
            </div>
          </div>

          <div className="form-field">
            <label>Режим</label>
            <div className="text-muted" style={{ fontSize: '0.7rem' }}>
              Cognitive (LLM)
            </div>
          </div>

          <div className="form-field">
            <div className="form-field-header">
              <label>S &amp; G (сим-конфиг)</label>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <button
                  className="btn-clipped small"
                  onClick={async () => {
                    setSimConfigLoading(true)
                    try {
                      const qs = new URLSearchParams({ governance: editing.governance })
                      const res = await apiClient.get(`/api/templates/scenarios/${editing.scenario}?${qs.toString()}`)
                      if (!res.ok) {
                        const text = await res.text().catch(() => '')
                        window.alert(text || 'Не удалось загрузить шаблон')
                        return
                      }
                      const cfg = await res.json().catch(() => null) as Record<string, unknown> | null
                      if (!cfg || typeof cfg !== 'object') {
                        window.alert('Шаблон вернул некорректные данные')
                        return
                      }
                      cfg.max_rounds = editing.rounds
                      if (editing.seed !== null) cfg.seed = editing.seed
                      setSimConfigText(JSON.stringify(cfg, null, 2))
                      setSimConfigError(null)
                    } finally {
                      setSimConfigLoading(false)
                    }
                  }}
                  disabled={simConfigLoading}
                  title="Подставить полный конфиг симуляции из встроенного шаблона (с учётом G)"
                >
                  {simConfigLoading ? '…' : '⭳ Из шаблона'}
                </button>
                <button
                  className="btn-clipped danger small"
                  onClick={() => {
                    if (!simConfigText) return
                    if (!window.confirm('Очистить сим-конфиг? Будет использован встроенный шаблон.')) return
                    setSimConfigText('')
                    setSimConfigError(null)
                  }}
                  title="Убрать кастомизацию (вернуться к S*/G*)"
                >
                  Очистить
                </button>
              </div>
            </div>
            <div className="text-muted" style={{ fontSize: '0.7rem', marginTop: '0.25rem' }}>
              Пусто = запуск по встроенным шаблонам S/G. Здесь можно увидеть и изменить «что зашито» (агенты, потребности, параметры).
            </div>
            <textarea
              className="hud-input"
              rows={10}
              value={simConfigText}
              onChange={(e) => {
                setSimConfigText(e.target.value)
                if (simConfigError) setSimConfigError(null)
              }}
              placeholder="(опционально) JSON ScenarioConfig"
              style={{ marginTop: '0.5rem', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", \"Courier New\", monospace' }}
            />
            {simConfigError && (
              <div style={{ marginTop: '0.5rem' }}>
                <span className="badge danger small">JSON: {simConfigError}</span>
              </div>
            )}
          </div>

          <div className="form-field">
            <div className="form-field-header">
              <label>Агенты ({editing.agents.length})</label>
              <button className="btn-clipped success small" onClick={addAgent}>+ Добавить</button>
            </div>
            <div className="agents-table">
              {editing.agents.map((agent, i) => (
                <div key={i} className="agent-row">
                  <input
                    className="hud-input"
                    placeholder="Имя"
                    value={agent.name}
                    onChange={(e) => updateAgent(i, 'name', e.target.value)}
                  />
                  <select
                    className="hud-input"
                    value={agent.role}
                    onChange={(e) => updateAgent(i, 'role', e.target.value)}
                  >
                    {ROLE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                  <input
                    className="hud-input"
                    type="number"
                    min={0} max={10} step={0.5}
                    value={agent.initial_reputation}
                    onChange={(e) => updateAgent(i, 'initial_reputation', Number(e.target.value))}
                    style={{ width: '70px' }}
                    disabled={agent.role === 'auditor'}
                    title={agent.role === 'auditor' ? 'У ИИ-аудитора нет репутации' : 'Начальная репутация'}
                  />
                  <button className="btn-clipped danger small" onClick={() => removeAgent(i)}>✕</button>
                </div>
              ))}
              {editing.agents.length === 0 && (
                <div style={{ padding: '0.75rem', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                  Нет агентов — нажмите «+ Добавить»
                </div>
              )}
            </div>
          </div>

          <div className="form-field">
            <button
              className="btn-clipped small"
              onClick={() => setShowJson(!showJson)}
              style={{ marginBottom: '0.5rem', width: 'fit-content' }}
            >
              {showJson ? '▲ Скрыть JSON' : '▼ JSON-превью'}
            </button>
            {showJson && (
              <pre className="json-preview">{JSON.stringify(editing, null, 2)}</pre>
            )}
          </div>
        </div>

        <div className="scenarios-editor-footer">
          <button
            className="btn-clipped primary"
            onClick={handleSave}
            disabled={saving || !editing.name}
          >
            {saving ? 'Сохранение...' : '✓ Сохранить'}
          </button>
          {editing.id && user?.role === 'admin' && (
            <button
              className="btn-clipped success"
              onClick={async () => {
                const snapshot: Scenario = { ...editing, runner: 'cognitive' }
                await handleSave()
                await handleRun(snapshot)
              }}
              disabled={saving || !editing.name}
            >
              ▶ Сохранить и запустить
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="scenarios-view">
      <div className="scenarios-header">
        <span>Сценарии ({scenarios === null ? '…' : scenarioList.length})</span>
        {user?.role === 'admin' && (
          <button className="btn-clipped primary" onClick={() => setEditing({ ...EMPTY_SCENARIO })}>
            + Создать сценарий
          </button>
        )}
      </div>

      {scenarioList.length === 0 && (
        <div className="scenarios-empty">
          <div className="text-muted">{scenarios === null ? 'Загрузка…' : 'Нет сохранённых сценариев'}</div>
          <div style={{ fontSize: '0.7rem', marginTop: '0.5rem', color: 'var(--text-muted)' }}>
            Создайте первый сценарий чтобы начать симуляцию
          </div>
        </div>
      )}

      <div className="scenarios-list">
        {scenarioList.map((s) => (
          <div key={s.id} className="scenario-card hud-panel">
            <div className="corner tl" /><div className="corner tr" />
            <div className="corner bl" /><div className="corner br" />
            <div className="scenario-card-body">
              <div className="scenario-card-title">{s.name}</div>
              {s.description && <div className="scenario-card-desc">{s.description}</div>}
              <div className="scenario-card-meta">
                {s.scenario && <span className="badge small accent">{s.scenario}</span>}
                {s.governance && <span className="badge small info">{s.governance}</span>}
                {s.sim_config && <span className="badge small warning" title="Есть кастомный сим-конфиг">custom</span>}
                <span className="badge small">{s.agents?.length ?? 0} аг.</span>
                <span className="badge small" title="Количество шагов симуляции (раундов)">{s.rounds} шагов</span>
                {s.seed !== null && <span className="badge small">seed {s.seed}</span>}
              </div>
            </div>
            <div className="scenario-card-actions">
              {user?.role === 'admin' && (
                <>
                  <button className="btn-clipped success small" onClick={() => handleRun(s)} title="Запустить">▶</button>
                  <button className="btn-clipped small" onClick={() => setEditing({ ...EMPTY_SCENARIO, ...s, runner: 'cognitive' })} title="Редактировать">✎</button>
                  <button className="btn-clipped danger small" onClick={() => s.id && handleDelete(s.id)} title="Удалить">✕</button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
