import { useEffect, useState } from 'react'
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
  rounds: number
  seed: number | null
  agents: Agent[]
}

const EMPTY_SCENARIO: Scenario = {
  name: '',
  description: '',
  rounds: 10,
  seed: null,
  agents: [],
}

const ROLE_OPTIONS = [
  { value: 'official', label: 'Чиновник' },
  { value: 'business', label: 'Подрядчик' },
  { value: 'auditor',  label: 'Аудитор' },
]

export function ScenariosView({ onLaunch, onStartLive, user }: {
  onLaunch?: () => void
  onStartLive?: () => void
  user: AuthUser | null
}) {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [editing, setEditing] = useState<Scenario | null>(null)
  const [showJson, setShowJson] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    apiClient.get('/api/scenarios').then((r) => r.json()).then(setScenarios).catch(() => {})
  }, [])

  async function handleSave() {
    if (!editing) return
    setSaving(true)
    try {
      const res = editing.id
        ? await apiClient.put(`/api/scenarios/${editing.id}`, editing)
        : await apiClient.post('/api/scenarios', editing)
      const saved: Scenario = await res.json()
      setScenarios((prev) =>
        editing.id
          ? prev.map((s) => (s.id === saved.id ? saved : s))
          : [...prev, saved]
      )
      setEditing(null)
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    await apiClient.delete(`/api/scenarios/${id}`)
    setScenarios((prev) => prev.filter((s) => s.id !== id))
  }

  async function handleRun(id: string) {
    try {
      const res = await apiClient.post(`/api/scenarios/${id}/run`)
      if (res.ok) {
        onLaunch?.()
        onStartLive?.()
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
    }
    setEditing({ ...editing, agents })
  }

  function removeAgent(i: number) {
    if (!editing) return
    setEditing({ ...editing, agents: editing.agents.filter((_, idx) => idx !== i) })
  }

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
              <label>Раундов</label>
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
                    title="Начальная репутация"
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
                await handleSave()
                if (editing.id) await handleRun(editing.id)
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
        <span>Сценарии ({scenarios.length})</span>
        {user?.role === 'admin' && (
          <button className="btn-clipped primary" onClick={() => setEditing({ ...EMPTY_SCENARIO })}>
            + Создать сценарий
          </button>
        )}
      </div>

      {scenarios.length === 0 && (
        <div className="scenarios-empty">
          <div className="text-muted">Нет сохранённых сценариев</div>
          <div style={{ fontSize: '0.7rem', marginTop: '0.5rem', color: 'var(--text-muted)' }}>
            Создайте первый сценарий чтобы начать симуляцию
          </div>
        </div>
      )}

      <div className="scenarios-list">
        {scenarios.map((s) => (
          <div key={s.id} className="scenario-card hud-panel">
            <div className="corner tl" /><div className="corner tr" />
            <div className="corner bl" /><div className="corner br" />
            <div className="scenario-card-body">
              <div className="scenario-card-title">{s.name}</div>
              {s.description && <div className="scenario-card-desc">{s.description}</div>}
              <div className="scenario-card-meta">
                <span className="badge small">{s.agents?.length ?? 0} аг.</span>
                <span className="badge small">{s.rounds} раундов</span>
                {s.seed !== null && <span className="badge small">seed {s.seed}</span>}
              </div>
            </div>
            <div className="scenario-card-actions">
              {user?.role === 'admin' && (
                <>
                  <button className="btn-clipped success small" onClick={() => s.id && handleRun(s.id)} title="Запустить">▶</button>
                  <button className="btn-clipped small" onClick={() => setEditing({ ...s })} title="Редактировать">✎</button>
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
