import { useEffect, useState } from 'react'
import { apiClient } from '../utils/apiClient'
import type { AuthUser } from '../hooks/useAuth'

interface Capability {
  action: string
  case_types: string[]
}

interface ResourceDefaults {
  budget_limit?: number
  staffing_slots?: number
  contract_capacity?: number
}

interface AgentType {
  id?: string
  name: string
  description?: string
  id_prefix?: string
  capabilities: Capability[]
  resources?: ResourceDefaults
}

const EMPTY_AGENT_TYPE: AgentType = {
  name: '',
  description: '',
  id_prefix: '',
  capabilities: [],
  resources: { budget_limit: 0, staffing_slots: 0, contract_capacity: 0 },
}

function normalizeCapability(raw: Capability): Capability | null {
  const action = (raw.action || '').trim()
  if (!action) return null
  const caseTypes = (raw.case_types || [])
    .map((s) => String(s).trim())
    .filter(Boolean)
  return { action, case_types: caseTypes }
}

export function AgentTypesView({ user }: { user: AuthUser | null }) {
  const [types, setTypes] = useState<AgentType[] | null>(null)
  const [editing, setEditing] = useState<AgentType | null>(null)
  const [showJson, setShowJson] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    apiClient.get('/api/agent-types')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setTypes(Array.isArray(data) ? data : []))
      .catch(() => setTypes([]))
  }, [])

  async function handleSave() {
    if (!editing) return
    const payload: AgentType = {
      ...editing,
      name: editing.name.trim(),
      description: (editing.description || '').trim(),
      id_prefix: (editing.id_prefix || '').trim(),
      capabilities: (editing.capabilities || [])
        .map(normalizeCapability)
        .filter(Boolean) as Capability[],
      resources: editing.resources ?? {},
    }
    if (!payload.name) return

    setSaving(true)
    try {
      const res = payload.id
        ? await apiClient.put(`/api/agent-types/${payload.id}`, payload)
        : await apiClient.post('/api/agent-types', payload)
      if (!res.ok) return
      const saved = await res.json().catch(() => null) as AgentType | null
      if (!saved) return

      setTypes((prev) => {
        const list = Array.isArray(prev) ? prev : []
        return payload.id
          ? list.map((t) => (t.id === saved.id ? saved : t))
          : [saved, ...list]
      })
      setEditing(null)
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(typeId: string) {
    if (!window.confirm('Удалить тип агента? Это действие необратимо.')) return
    await apiClient.delete(`/api/agent-types/${typeId}`)
    setTypes((prev) => (Array.isArray(prev) ? prev.filter((t) => t.id !== typeId) : []))
  }

  function addCapability() {
    if (!editing) return
    setEditing({
      ...editing,
      capabilities: [...editing.capabilities, { action: '', case_types: [] }],
    })
  }

  function updateCapability(i: number, patch: Partial<Capability>) {
    if (!editing) return
    const capabilities = editing.capabilities.map((c, idx) => (idx === i ? { ...c, ...patch } : c))
    setEditing({ ...editing, capabilities })
  }

  function removeCapability(i: number) {
    if (!editing) return
    setEditing({ ...editing, capabilities: editing.capabilities.filter((_, idx) => idx !== i) })
  }

  const list = types ?? []

  if (editing) {
    return (
      <div className="library-editor">
        <div className="library-editor-header">
          <span>{editing.id ? 'Редактировать тип агента' : 'Новый тип агента'}</span>
          <button className="btn-clipped small" onClick={() => setEditing(null)}>✕ Отмена</button>
        </div>

        <div className="scenarios-form">
          <div className="form-field">
            <label>Название</label>
            <input
              className="hud-input"
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              placeholder="Например: Чиновник"
            />
          </div>

          <div className="form-field">
            <label>Описание</label>
            <textarea
              className="hud-input"
              rows={2}
              value={editing.description ?? ''}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              placeholder="Коротко: чем отличается этот тип"
            />
          </div>

          <div className="form-row">
            <div className="form-field">
              <label>Префикс id (опционально)</label>
              <input
                className="hud-input"
                value={editing.id_prefix ?? ''}
                onChange={(e) => setEditing({ ...editing, id_prefix: e.target.value })}
                placeholder="off / biz / aud"
              />
            </div>
            <div className="form-field">
              <label>Ресурсы (по умолчанию)</label>
              <div className="text-muted" style={{ fontSize: '0.7rem' }}>
                budget / staff / contracts
              </div>
            </div>
          </div>

          <div className="form-row">
            <div className="form-field">
              <label>Budget limit</label>
              <input
                className="hud-input"
                type="number"
                value={editing.resources?.budget_limit ?? 0}
                onChange={(e) => setEditing({
                  ...editing,
                  resources: { ...(editing.resources ?? {}), budget_limit: Number(e.target.value) },
                })}
              />
            </div>
            <div className="form-field">
              <label>Staffing slots</label>
              <input
                className="hud-input"
                type="number"
                value={editing.resources?.staffing_slots ?? 0}
                onChange={(e) => setEditing({
                  ...editing,
                  resources: { ...(editing.resources ?? {}), staffing_slots: Number(e.target.value) },
                })}
              />
            </div>
            <div className="form-field">
              <label>Contract capacity</label>
              <input
                className="hud-input"
                type="number"
                value={editing.resources?.contract_capacity ?? 0}
                onChange={(e) => setEditing({
                  ...editing,
                  resources: { ...(editing.resources ?? {}), contract_capacity: Number(e.target.value) },
                })}
              />
            </div>
          </div>

          <div className="form-field">
            <div className="form-field-header">
              <label>Полномочия ({editing.capabilities.length})</label>
              <button className="btn-clipped success small" onClick={addCapability}>+ Добавить</button>
            </div>
            <div className="agents-table">
              {editing.capabilities.map((cap, i) => (
                <div key={i} className="agent-row">
                  <input
                    className="hud-input"
                    placeholder="action (open_case, submit_proposal, audit...)"
                    value={cap.action}
                    onChange={(e) => updateCapability(i, { action: e.target.value })}
                  />
                  <input
                    className="hud-input"
                    placeholder="case_types (через запятую)"
                    value={(cap.case_types ?? []).join(', ')}
                    onChange={(e) => updateCapability(i, {
                      case_types: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })}
                  />
                  <button className="btn-clipped danger small" onClick={() => removeCapability(i)}>✕</button>
                </div>
              ))}
              {editing.capabilities.length === 0 && (
                <div style={{ padding: '0.75rem', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                  Нет полномочий — нажмите «+ Добавить»
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
            disabled={saving || !editing.name.trim()}
          >
            {saving ? 'Сохранение...' : '✓ Сохранить'}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="library-view">
      <div className="library-header">
        <span>Типы агентов ({types === null ? '…' : list.length})</span>
        {user?.role === 'admin' && (
          <button className="btn-clipped primary" onClick={() => setEditing({ ...EMPTY_AGENT_TYPE })}>
            + Создать тип
          </button>
        )}
      </div>

      {list.length === 0 && (
        <div className="scenarios-empty">
          <div className="text-muted">{types === null ? 'Загрузка…' : 'Нет типов агентов'}</div>
          <div style={{ fontSize: '0.7rem', marginTop: '0.5rem', color: 'var(--text-muted)' }}>
            Типы агентов нужны для сборки сценариев из модулей (роль → полномочия → ресурсы).
          </div>
        </div>
      )}

      <div className="library-list">
        {list.map((t) => (
          <div key={t.id ?? t.name} className="library-card hud-panel">
            <div className="corner tl" /><div className="corner tr" />
            <div className="corner bl" /><div className="corner br" />
            <div className="scenario-card-body">
              <div className="scenario-card-title">{t.name}</div>
              {t.description && <div className="scenario-card-desc">{t.description}</div>}
              <div className="scenario-card-meta">
                <span className="badge small">{(t.capabilities?.length ?? 0)} cap.</span>
                {t.id_prefix && <span className="badge small info">{t.id_prefix}</span>}
              </div>
            </div>
            <div className="scenario-card-actions">
              {user?.role === 'admin' && (
                <>
                  <button className="btn-clipped small" onClick={() => setEditing({ ...EMPTY_AGENT_TYPE, ...t })} title="Редактировать">✎</button>
                  {t.id && (
                    <button className="btn-clipped danger small" onClick={() => handleDelete(t.id!)} title="Удалить">✕</button>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

