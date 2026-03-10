import { useEffect, useRef, useState } from 'react'
import { apiClient } from '../utils/apiClient'
import type { AuthUser } from '../hooks/useAuth'

interface AgentTypeOption {
  id: string
  name: string
  id_prefix?: string
}

interface Agent {
  id: string
  name: string
  role: string
  initial_reputation: number
  position?: string
  personality_archetype?: string | null
}

interface PersonalityOption {
  id: string
  name?: string
  has_interview?: boolean
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
  parallel_agents?: boolean
  parallel_workers?: number | null
  parallel_window?: number | null
}

interface TemplateScenario {
  id: string
  title: string
  description?: string
}

interface GovernanceModeItem {
  id: string
  label: string
  description?: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

async function readApiErrorMessage(res: Response): Promise<string> {
  try {
    const payload = await res.json()
    if (isRecord(payload) && typeof payload.detail === 'string' && payload.detail.trim()) {
      return payload.detail
    }
  } catch {
    // ignore parse errors
  }
  try {
    const text = await res.text()
    if (text.trim()) {
      return text
    }
  } catch {
    // ignore read errors
  }
  return ''
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

const BUILTIN_ROLES: AgentTypeOption[] = [
  { id: 'official', name: 'Чиновник', id_prefix: 'off' },
  { id: 'business', name: 'Подрядчик', id_prefix: 'biz' },
  { id: 'auditor',  name: 'Аудитор', id_prefix: 'aud' },
]

const FALLBACK_SCENARIOS: TemplateScenario[] = [
  { id: 'S0', title: 'Чистая сделка' },
  { id: 'S1', title: 'Прямой сговор' },
  { id: 'S2', title: 'Кумовство при найме' },
]

const FALLBACK_GOVERNANCE: GovernanceModeItem[] = [
  { id: 'G0', label: 'G0 — Без контроля' },
  { id: 'G1', label: 'G1 — Аудитор (рекомендательный)' },
  { id: 'G2', label: 'G2 — Аудитор (санкции по репутации)' },
  { id: 'G3', label: 'G3 — Полный контроль (трибунал)' },
]

export function ScenariosView({ onLaunch, onGoLive, user }: {
  onLaunch?: () => void
  onGoLive?: (runName?: string) => void
  user: AuthUser | null
}) {
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null)
  const [templateScenarios, setTemplateScenarios] = useState<TemplateScenario[] | null>(null)
  const [governanceModes, setGovernanceModes] = useState<GovernanceModeItem[] | null>(null)
  const [agentTypes, setAgentTypes] = useState<AgentTypeOption[]>(BUILTIN_ROLES)
  const [personalities, setPersonalities] = useState<PersonalityOption[]>([])
  const [editing, setEditing] = useState<Scenario | null>(null)
  const [showJson, setShowJson] = useState(false)
  const [saving, setSaving] = useState(false)
  const [simConfigText, setSimConfigText] = useState('')
  const [simConfigError, setSimConfigError] = useState<string | null>(null)
  const [simConfigLoading, setSimConfigLoading] = useState(false)
  const [secondaryPrompt, setSecondaryPrompt] = useState('')
  const [secondaryFamily, setSecondaryFamily] = useState('0')
  const [secondarySociety, setSecondarySociety] = useState('0')
  const [secondaryReplace, setSecondaryReplace] = useState(true)
  const [secondaryLoading, setSecondaryLoading] = useState(false)
  const prevEditingRef = useRef<Scenario | null>(null)

  useEffect(() => {
    if (editing && prevEditingRef.current === null) {
      setSimConfigText(editing.sim_config ? JSON.stringify(editing.sim_config, null, 2) : '')
      setSimConfigError(null)
      setSecondaryPrompt('')
      setSecondaryFamily('0')
      setSecondarySociety('0')
      setSecondaryReplace(true)
    }
    if (!editing && prevEditingRef.current !== null) {
      setSimConfigText('')
      setSimConfigError(null)
      setSecondaryPrompt('')
      setSecondaryFamily('0')
      setSecondarySociety('0')
      setSecondaryReplace(true)
    }
    prevEditingRef.current = editing
  }, [editing])

  useEffect(() => {
    apiClient.get('/api/scenarios')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setScenarios(Array.isArray(data) ? data : []))
      .catch(() => setScenarios([]))
  }, [])

  useEffect(() => {
    apiClient.get('/api/templates/scenarios')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => {
        if (Array.isArray(data)) {
          setTemplateScenarios(
            data
              .map((x): TemplateScenario | null => {
                if (!isRecord(x)) return null
                const idRaw = x.id
                const id = typeof idRaw === 'string' ? idRaw : String(idRaw ?? '')
                if (!id) return null
                const titleRaw = x.title
                const title = typeof titleRaw === 'string' ? titleRaw : id
                const description = typeof x.description === 'string' ? x.description : undefined
                return description ? { id, title, description } : { id, title }
              })
              .filter((x): x is TemplateScenario => x !== null),
          )
          return
        }
        setTemplateScenarios([])
      })
      .catch(() => setTemplateScenarios([]))

    apiClient.get('/api/personalities')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => {
        if (Array.isArray(data)) {
          setPersonalities(
            data
              .filter((x): x is Record<string, unknown> => isRecord(x) && typeof x.id === 'string')
              .map((x) => ({
                id: String(x.id),
                name: typeof x.name === 'string' ? x.name : undefined,
                has_interview: typeof x.has_interview === 'boolean' ? x.has_interview : false,
              })),
          )
        }
      })
      .catch(() => {})

    apiClient.get('/api/agent-types')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          const types: AgentTypeOption[] = data
            .filter((x): x is Record<string, unknown> => isRecord(x) && typeof x.name === 'string')
            .map((x) => ({
              id: String(x.id ?? x.name),
              name: String(x.name),
              id_prefix: typeof x.id_prefix === 'string' ? x.id_prefix : undefined,
            }))
          if (types.length > 0) setAgentTypes(types)
        }
      })
      .catch(() => {})

    apiClient.get('/api/templates/governance')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => {
        if (Array.isArray(data)) {
          setGovernanceModes(
            data
              .map((x): GovernanceModeItem | null => {
                if (!isRecord(x)) return null
                const idRaw = x.id
                const id = typeof idRaw === 'string' ? idRaw : String(idRaw ?? '')
                if (!id) return null
                const labelRaw = x.label
                const label = typeof labelRaw === 'string' ? labelRaw : id
                const description = typeof x.description === 'string' ? x.description : undefined
                return description ? { id, label, description } : { id, label }
              })
              .filter((x): x is GovernanceModeItem => x !== null),
          )
          return
        }
        setGovernanceModes([])
      })
      .catch(() => setGovernanceModes([]))
  }, [])

  async function handleSave(): Promise<Scenario | null> {
    if (!editing) return null
    let simConfig: Record<string, unknown> | undefined
    if (simConfigText.trim()) {
      try {
        const parsed = JSON.parse(simConfigText) as unknown
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          setSimConfigError('Ожидается JSON-объект (ScenarioConfig)')
          return null
        }
        simConfig = parsed as Record<string, unknown>
      } catch (e) {
        setSimConfigError(e instanceof Error ? e.message : 'Некорректный JSON')
        return null
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
      if (!res.ok) {
        const message = await readApiErrorMessage(res)
        window.alert(message || 'Не удалось сохранить сценарий')
        return null
      }
      const saved: Scenario = await res.json()
      setScenarios((prev) => {
        const list = Array.isArray(prev) ? prev : []
        return editing.id
          ? list.map((s) => (s.id === saved.id ? saved : s))
          : [...list, saved]
      })
      setEditing(null)
      return saved
    } catch {
      window.alert('Ошибка сети при сохранении сценария')
      return null
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
      if (!res.ok) {
        const message = await readApiErrorMessage(res)
        window.alert(message || 'Не удалось запустить прогон')
        return
      }
      const data = await res.json().catch(() => null) as { run_name?: string } | null
      onLaunch?.()
      if (data?.run_name) onGoLive?.(data.run_name)
    } catch {
      window.alert('Ошибка сети при запуске прогона')
    }
  }

  /** Найти тип агента по role — ищем совпадение по id, id_prefix или вхождению подстроки. */
  function _findAgentType(role: string): AgentTypeOption | undefined {
    if (!role) return undefined
    const r = role.toLowerCase()
    return agentTypes.find((t) => t.id === role)
      ?? agentTypes.find((t) => t.id_prefix === role)
      ?? agentTypes.find((t) => t.id.toLowerCase().startsWith(r) || r.startsWith(t.id.toLowerCase()))
  }

  function _prefixForRole(role: string): string {
    const match = _findAgentType(role)
    if (match?.id_prefix) return match.id_prefix
    return role.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 4) || 'ag'
  }

  function addAgent() {
    if (!editing) return
    const idx = editing.agents.length + 1
    const defaultType = agentTypes[0] ?? BUILTIN_ROLES[0]
    setEditing({
      ...editing,
      agents: [...editing.agents, {
        id: `${_prefixForRole(defaultType.id)}_${idx}`,
        name: `Агент ${idx}`,
        role: defaultType.id,
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
      const prefix = _prefixForRole(agents[i].role)
      agents[i].id = `${prefix}_${String(value).toLowerCase().replace(/\s+/g, '_').slice(0, 12)}`
    }
    if (field === 'role') {
      const prefix = _prefixForRole(String(value))
      agents[i].id = `${prefix}_${agents[i].name.toLowerCase().replace(/\s+/g, '_').slice(0, 12)}`
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
                {(templateScenarios ?? FALLBACK_SCENARIOS).map((o) => (
                  <option key={o.id} value={o.id} title={o.description || ''}>
                    {o.id} — {o.title}
                  </option>
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
                {(governanceModes ?? FALLBACK_GOVERNANCE).map((o) => (
                  <option key={o.id} value={o.id} title={o.description || ''}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="form-row">
            <div className="form-field">
              <label title="Длительность симуляции в днях (временная модель)">Дней</label>
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
                      cfg.ticks = editing.rounds
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
            {simConfigText.trim() && (
              <div className="text-muted" style={{ fontSize: '0.7rem', marginTop: '0.35rem' }}>
                При заполненном JSON источником истины становится `ScenarioConfig`.
                Поля формы синхронизируют основные метаданные, governance и состав агентов; точечные LC-настройки редактируются в JSON.
              </div>
            )}
            <textarea
              className="hud-input"
              rows={10}
              value={simConfigText}
              onChange={(e) => {
                setSimConfigText(e.target.value)
                if (simConfigError) setSimConfigError(null)
              }}
              placeholder="(опционально) JSON ScenarioConfig"
              style={{ marginTop: '0.5rem', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace' }}
            />
            {simConfigError && (
              <div style={{ marginTop: '0.5rem' }}>
                <span className="badge danger small">JSON: {simConfigError}</span>
              </div>
            )}
          </div>

          <div className="form-field">
            <div className="form-field-header">
              <label>Вторичные агенты (LLM)</label>
              <button
                className="btn-clipped primary small"
                onClick={async () => {
                  const familyCount = Math.max(0, Number(secondaryFamily) || 0)
                  const societyCount = Math.max(0, Number(secondarySociety) || 0)
                  if ((familyCount + societyCount) <= 0) return
                  if (!secondaryPrompt.trim()) {
                    window.alert('Опишите среду/контекст в промпте (хотя бы 1–2 предложения).')
                    return
                  }

                  let simConfig: Record<string, unknown> | null = null
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

                  setSecondaryLoading(true)
                  try {
                    const res = await apiClient.post('/api/ai/secondary-agents', {
                      scenario: editing.scenario,
                      governance: editing.governance,
                      seed: editing.seed,
                      rounds: editing.rounds,
                      prompt: secondaryPrompt,
                      family_count: familyCount,
                      society_count: societyCount,
                      replace_existing: secondaryReplace,
                      sim_config: simConfig,
                    })
                    if (!res.ok) {
                      const text = await res.text().catch(() => '')
                      window.alert(text || 'Не удалось сгенерировать вторичных агентов')
                      return
                    }
                    const cfg = await res.json().catch(() => null) as Record<string, unknown> | null
                    if (!cfg || typeof cfg !== 'object') {
                      window.alert('Сервер вернул некорректные данные')
                      return
                    }
                    setSimConfigText(JSON.stringify(cfg, null, 2))
                    setSimConfigError(null)
                  } finally {
                    setSecondaryLoading(false)
                  }
                }}
                disabled={
                  secondaryLoading
                  || ((Number(secondaryFamily) || 0) + (Number(secondarySociety) || 0) <= 0)
                  || !secondaryPrompt.trim()
                }
                title="Добавить fam_*/soc_* агентов в сим-конфиг (или создать конфиг из шаблона) через LLM"
              >
                {secondaryLoading ? '…' : '+ Сгенерировать'}
              </button>
            </div>
            <div className="text-muted" style={{ fontSize: '0.7rem', marginTop: '0.25rem' }}>
              Генератор добавляет вторичных агентов (fam_*/soc_*) и обновляет JSON ScenarioConfig.
              Рекомендуется включать контекст среды: город/организация/давление/«нормы».
            </div>

            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.5rem' }}>
              <div className="form-field" style={{ margin: 0 }}>
                <label>Семья (fam_*)</label>
                <input
                  className="hud-input"
                  type="number"
                  min={0}
                  max={20}
                  value={secondaryFamily}
                  onChange={(e) => setSecondaryFamily(e.target.value)}
                />
              </div>
              <div className="form-field" style={{ margin: 0 }}>
                <label>Общество (soc_*)</label>
                <input
                  className="hud-input"
                  type="number"
                  min={0}
                  max={20}
                  value={secondarySociety}
                  onChange={(e) => setSecondarySociety(e.target.value)}
                />
              </div>
              <div className="form-field" style={{ margin: 0, alignSelf: 'end' }}>
                <label style={{ opacity: 0 }}>replace</label>
                <label style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', fontSize: '0.75rem', textTransform: 'none', letterSpacing: 0 }}>
                  <input
                    type="checkbox"
                    checked={secondaryReplace}
                    onChange={() => setSecondaryReplace((v) => !v)}
                  />
                  <span className="text-muted">Заменить существующих fam_/soc_</span>
                </label>
              </div>
            </div>

            <textarea
              className="hud-input"
              rows={4}
              value={secondaryPrompt}
              onChange={(e) => setSecondaryPrompt(e.target.value)}
              placeholder="Контекст среды: организация, город, нормы, давление, риски, медиа/общественное мнение... (и любые пожелания к вторичным агентам)"
              style={{ marginTop: '0.5rem' }}
            />
          </div>

          <div className="form-field">
            <div className="form-field-header">
              <label>Параллелизация</label>
            </div>
            <div className="text-muted" style={{ fontSize: '0.7rem', marginTop: '0.25rem' }}>
              Параллельная генерация решений агентами. Если не включено, используются переменные окружения.
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.5rem', alignItems: 'end' }}>
              <div className="form-field" style={{ margin: 0 }}>
                <label style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', fontSize: '0.75rem', textTransform: 'none', letterSpacing: 0 }}>
                  <input
                    type="checkbox"
                    checked={editing.parallel_agents ?? false}
                    onChange={() => setEditing({ ...editing, parallel_agents: !editing.parallel_agents })}
                  />
                  <span>Параллельные агенты</span>
                </label>
              </div>
              <div className="form-field" style={{ margin: 0 }}>
                <label>Потоки</label>
                <input
                  className="hud-input"
                  type="number"
                  min={1} max={32}
                  value={editing.parallel_workers ?? ''}
                  onChange={(e) => setEditing({ ...editing, parallel_workers: e.target.value ? Number(e.target.value) : null })}
                  placeholder="авто"
                  disabled={!editing.parallel_agents}
                  style={{ width: '80px' }}
                />
              </div>
              <div className="form-field" style={{ margin: 0 }}>
                <label title="Окно батчирования в секундах симулированного времени">Окно (сек)</label>
                <input
                  className="hud-input"
                  type="number"
                  min={0} max={86400} step={60}
                  value={editing.parallel_window ?? ''}
                  onChange={(e) => setEditing({ ...editing, parallel_window: e.target.value ? Number(e.target.value) : null })}
                  placeholder="0"
                  disabled={!editing.parallel_agents}
                  style={{ width: '100px' }}
                />
              </div>
            </div>
          </div>

          <div className="form-field">
            <div className="form-field-header">
              <label>Агенты ({editing.agents.length})</label>
              <button className="btn-clipped success small" onClick={addAgent}>+ Добавить</button>
            </div>
            <div className="agents-table">
              {editing.agents.map((agent, i) => {
                const matchedType = _findAgentType(agent.role)
                const selectValue = matchedType ? matchedType.id : '__custom__'
                return (
                  <div key={i} className="agent-row" style={{ flexWrap: 'wrap', gap: '0.35rem' }}>
                    <input
                      className="hud-input"
                      placeholder="Имя"
                      value={agent.name}
                      onChange={(e) => updateAgent(i, 'name', e.target.value)}
                      style={{ flex: '1 1 120px', minWidth: '100px' }}
                    />
                    <select
                      className="hud-input"
                      value={selectValue}
                      onChange={(e) => {
                        if (e.target.value === '__custom__') {
                          updateAgent(i, 'role', '')
                        } else {
                          updateAgent(i, 'role', e.target.value)
                        }
                      }}
                      style={{ flex: '0 0 130px' }}
                      title="Тип агента (из библиотеки или свой)"
                    >
                      {agentTypes.map((t) => (
                        <option key={t.id} value={t.id}>{t.name}</option>
                      ))}
                      <option value="__custom__">Свой тип…</option>
                    </select>
                    {!matchedType && (
                      <input
                        className="hud-input"
                        placeholder="Роль / промпт агента (напр. «заместитель мэра по ЖКХ»)"
                        value={agent.role}
                        onChange={(e) => updateAgent(i, 'role', e.target.value)}
                        style={{ flex: '1 1 200px', minWidth: '180px' }}
                        title="Свободное описание роли — станет промптом агента в симуляции"
                      />
                    )}
                    <input
                      className="hud-input"
                      type="number"
                      min={0} max={10} step={0.5}
                      value={agent.initial_reputation}
                      onChange={(e) => updateAgent(i, 'initial_reputation', Number(e.target.value))}
                      style={{ width: '70px' }}
                      title="Начальная репутация"
                    />
                    <select
                      className="hud-input"
                      value={agent.personality_archetype ?? ''}
                      onChange={(e) => {
                        const val = e.target.value || null
                        if (!editing) return
                        const agents = editing.agents.map((a, idx) =>
                          idx === i ? { ...a, personality_archetype: val } : a,
                        )
                        setEditing({ ...editing, agents })
                      }}
                      style={{ flex: '0 0 160px' }}
                      title="Личность (из библиотеки). Пусто = сгенерировать при запуске."
                    >
                      <option value="">Авто</option>
                      {personalities.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name || p.id.slice(0, 8)}{p.has_interview ? '' : ' (нет интервью)'}
                        </option>
                      ))}
                    </select>
                    <button className="btn-clipped danger small" onClick={() => removeAgent(i)}>✕</button>
                  </div>
                )
              })}
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
              onClick={() => { void handleSave() }}
              disabled={saving || !editing.name}
            >
              {saving ? 'Сохранение...' : '✓ Сохранить'}
            </button>
          {editing.id && user?.role === 'admin' && (
            <button
              className="btn-clipped success"
              onClick={async () => {
                const saved = await handleSave()
                if (saved) {
                  await handleRun(saved)
                }
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
                <span className="badge small" title="Длительность симуляции (в днях)">{s.rounds} дн.</span>
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
