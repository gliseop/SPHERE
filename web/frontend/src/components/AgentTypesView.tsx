import { useEffect, useMemo, useRef, useState } from 'react'
import { apiClient } from '../utils/apiClient'
import { fetchPromptTemplates, renderPromptTemplate, type PromptTemplateBundle } from '../utils/promptTemplates'
import type { AuthUser } from '../hooks/useAuth'

const ACTIVE_PERSONALITY_STORAGE_KEY = 'sphere-active-personality-id'

interface AgentType {
  id?: string
  name: string
  description?: string
  id_prefix?: string
  personality_archetype?: string
}

interface PersonalityOption {
  id: string
  name: string
  description?: string
  biography?: string
  hexaco?: Record<string, number>
  dark_triad?: Record<string, number>
  neutralization_techniques?: string[]
}

const EMPTY_AGENT_TYPE: AgentType = {
  name: '',
  description: '',
  id_prefix: '',
  personality_archetype: '',
}

function safeJson(value: unknown, maxLen: number = 4000): string {
  try {
    const text = JSON.stringify(value, null, 2)
    return text.length > maxLen ? text.slice(0, maxLen) + '\n…' : text
  } catch {
    const text = String(value ?? '')
    return text.length > maxLen ? text.slice(0, maxLen) + '…' : text
  }
}

function defaultGenerateAgentTypeUserPrompt(
  templates: PromptTemplateBundle | null,
  personality: PersonalityOption | null,
  description: string,
): string {
  const personalityBlock = personality
    ? safeJson({
        id: personality.id,
        name: personality.name,
        description: personality.description ?? '',
        biography: personality.biography ?? '',
        hexaco: personality.hexaco ?? {},
        dark_triad: personality.dark_triad ?? {},
        neutralization_techniques: personality.neutralization_techniques ?? [],
      })
    : '(личность не выбрана)'

  const template = templates?.generate_agent_type?.user_default ?? ''
  return renderPromptTemplate(template, {
    personality_json: personalityBlock,
    description,
  }).trim()
}

export function AgentTypesView({ user }: { user: AuthUser | null }) {
  const [types, setTypes] = useState<AgentType[] | null>(null)
  const [editing, setEditing] = useState<AgentType | null>(null)
  const [showJson, setShowJson] = useState(false)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [promptTemplates, setPromptTemplates] = useState<PromptTemplateBundle | null>(null)

  const [personalities, setPersonalities] = useState<PersonalityOption[] | null>(null)
  const [activePersonalityId, setActivePersonalityId] = useState<string>(() => {
    try {
      return localStorage.getItem(ACTIVE_PERSONALITY_STORAGE_KEY) ?? ''
    } catch {
      return ''
    }
  })

  const [genSystemPrompt, setGenSystemPrompt] = useState('')
  const [genUserPrompt, setGenUserPrompt] = useState('')
  const [genSystemDirty, setGenSystemDirty] = useState(false)
  const [genUserDirty, setGenUserDirty] = useState(false)
  const prevEditingKeyRef = useRef<string | null>(null)

  useEffect(() => {
    apiClient.get('/api/agent-types')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setTypes(Array.isArray(data) ? data : []))
      .catch(() => setTypes([]))
  }, [])

  useEffect(() => {
    apiClient.get('/api/personalities')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setPersonalities(Array.isArray(data) ? data : []))
      .catch(() => setPersonalities([]))
  }, [])

  useEffect(() => {
    fetchPromptTemplates().then((data) => setPromptTemplates(data))
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(ACTIVE_PERSONALITY_STORAGE_KEY, activePersonalityId)
    } catch {
      // ignore
    }
  }, [activePersonalityId])

  const effectivePersonalityId = (editing?.personality_archetype || activePersonalityId || '').trim()
  const effectivePersonality = useMemo(() => {
    if (!effectivePersonalityId) return null
    return (personalities ?? []).find((p) => p && p.id === effectivePersonalityId) ?? null
  }, [personalities, effectivePersonalityId])

  const editingKey = editing ? (editing.id ?? '__new__') : null
  useEffect(() => {
    if (!editing || !editingKey) {
      prevEditingKeyRef.current = null
      return
    }
    if (prevEditingKeyRef.current === editingKey) return
    setGenSystemPrompt(promptTemplates?.generate_agent_type?.system_default ?? '')
    setGenUserPrompt(defaultGenerateAgentTypeUserPrompt(promptTemplates, effectivePersonality, editing.description ?? ''))
    setGenSystemDirty(false)
    setGenUserDirty(false)
    prevEditingKeyRef.current = editingKey
  }, [editing, editingKey, effectivePersonality, promptTemplates])

  useEffect(() => {
    if (!editingKey) return
    if (genUserDirty) return
    setGenUserPrompt(defaultGenerateAgentTypeUserPrompt(promptTemplates, effectivePersonality, editing?.description ?? ''))
  }, [editingKey, editing?.description, genUserDirty, effectivePersonality, promptTemplates])

  useEffect(() => {
    if (genSystemDirty) return
    setGenSystemPrompt(promptTemplates?.generate_agent_type?.system_default ?? '')
  }, [genSystemDirty, promptTemplates])

  async function handleSave() {
    if (!editing) return
    const payload: AgentType = {
      ...(editing.id ? { id: editing.id } : {}),
      name: editing.name.trim(),
      description: (editing.description || '').trim(),
      id_prefix: (editing.id_prefix || '').trim(),
      personality_archetype: (editing.personality_archetype || '').trim() || undefined,
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

  async function handleGenerate() {
    if (!editing) return
    if (!effectivePersonalityId) {
      window.alert('Выберите личность (вкладка «Личности») или укажите её здесь.')
      return
    }
    if (!editing.description?.trim()) return

    setGenerating(true)
    try {
      const res = await apiClient.post('/api/ai/generate-agent-type', {
        personality_id: effectivePersonalityId,
        description: editing.description.trim(),
        system_prompt: genSystemPrompt.trim(),
        user_prompt: genUserPrompt.trim(),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        window.alert(text || 'Не удалось сгенерировать тип')
        return
      }
      const data = await res.json().catch(() => null) as Record<string, unknown> | null
      if (!data || typeof data !== 'object') return

      setEditing((prev) => {
        if (!prev) return prev
        const name = typeof data.name === 'string' ? data.name : ''
        const description = typeof data.description === 'string' ? data.description : ''
        const id_prefix = typeof data.id_prefix === 'string' ? data.id_prefix : ''
        return {
          ...prev,
          name: prev.name?.trim() ? prev.name : (name || prev.name),
          description: description || prev.description,
          id_prefix: prev.id_prefix?.trim() ? prev.id_prefix : (id_prefix || prev.id_prefix),
          personality_archetype: effectivePersonalityId,
        }
      })
    } finally {
      setGenerating(false)
    }
  }

  const list = types ?? []

  if (editing) {
    return (
      <div className="library-editor">
        <div className="library-editor-header">
          <span>{editing.id ? 'Редактировать тип агента' : 'Новый тип агента'}</span>
          <button className="btn-clipped small" onClick={() => setEditing(null)}>Отмена</button>
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
            <div className="form-field-header">
              <label>Описание</label>
              {user?.role === 'admin' && (
                <button
                  className="btn-clipped primary small"
                  onClick={handleGenerate}
                  disabled={generating || !editing.description?.trim() || !effectivePersonalityId}
                  title="Сгенерировать тип по личности + описанию (LLM). Полномочия/ресурсы генерирует движок мира."
                  type="button"
                >
                  {generating ? '…' : 'Сгенерировать'}
                </button>
              )}
            </div>
            <textarea
              className="hud-input"
              rows={2}
              value={editing.description ?? ''}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              placeholder="Опишите роль/контекст — генератор развернёт это в тип агента"
            />
            {user?.role === 'admin' && (
              <details className="md-details">
                <summary className="md-summary">Промпт генерации типа (system/user) — можно подправить</summary>
                <div className="hud-panel compact" style={{ padding: '0.75rem' }}>
                  <div className="form-field" style={{ margin: 0 }}>
                    <div className="form-field-header">
                      <label>System prompt</label>
                      <button
                        className="btn-clipped small"
                        onClick={() => {
                          setGenSystemPrompt(promptTemplates?.generate_agent_type?.system_default ?? '')
                          setGenUserPrompt(defaultGenerateAgentTypeUserPrompt(promptTemplates, effectivePersonality, editing.description ?? ''))
                          setGenSystemDirty(false)
                          setGenUserDirty(false)
                        }}
                        type="button"
                        title="Сбросить промпт к значениям по умолчанию"
                      >
                        ↺ Сбросить
                      </button>
                    </div>
                    <textarea
                      className="hud-input"
                      rows={5}
                      value={genSystemPrompt}
                      onChange={(e) => {
                        setGenSystemPrompt(e.target.value)
                        if (!genSystemDirty) setGenSystemDirty(true)
                      }}
                      placeholder="system prompt"
                    />
                  </div>
                  <div className="form-field" style={{ marginTop: '0.5rem' }}>
                    <label>User prompt</label>
                    <textarea
                      className="hud-input"
                      rows={6}
                      value={genUserPrompt}
                      onChange={(e) => {
                        setGenUserPrompt(e.target.value)
                        if (!genUserDirty) setGenUserDirty(true)
                      }}
                      placeholder="user prompt"
                    />
                  </div>
                  <div className="text-muted" style={{ fontSize: '0.65rem', marginTop: '0.35rem' }}>
                    В генерацию уйдут именно эти system/user промпты.
                  </div>
                </div>
              </details>
            )}
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
              <label>Личность (архетип)</label>
              <select
                className="hud-input"
                value={editing.personality_archetype ?? ''}
                onChange={(e) => {
                  const next = e.target.value
                  setEditing({ ...editing, personality_archetype: next })
                  setActivePersonalityId(next)
                }}
              >
                <option value="">— не выбрано —</option>
                {(personalities ?? []).map((p) => (
                  <option key={p.id} value={p.id} title={p.description || ''}>{p.name}</option>
                ))}
              </select>
              <div className="text-muted" style={{ fontSize: '0.7rem', marginTop: '0.25rem' }}>
                Полномочия и ресурсы генерируются движком мира при запуске прогона.
              </div>
            </div>
          </div>

          <div className="form-field">
            <button
              className="btn-clipped small"
            onClick={() => setShowJson(!showJson)}
            style={{ marginBottom: '0.5rem', width: 'fit-content' }}
            type="button"
          >
              {showJson ? 'Скрыть JSON' : 'JSON-превью'}
            </button>
            {showJson && (
              <pre className="json-preview">{safeJson(editing, 20_000)}</pre>
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
          <button
            className="btn-clipped primary"
            onClick={() => setEditing({ ...EMPTY_AGENT_TYPE, personality_archetype: activePersonalityId || '' })}
          >
            + Создать тип
          </button>
        )}
      </div>

      {list.length === 0 && (
        <div className="scenarios-empty">
          <div className="text-muted">{types === null ? 'Загрузка…' : 'Нет типов агентов'}</div>
          <div style={{ fontSize: '0.7rem', marginTop: '0.5rem', color: 'var(--text-muted)' }}>
            Типы агентов задают роль и базовую личность. Полномочия и ресурсы генерируются движком мира.
          </div>
        </div>
      )}

      <div className="library-list">
        {list.map((t) => {
          const raw = t as unknown as Record<string, unknown>
          const personalityId = typeof raw.personality_archetype === 'string' ? raw.personality_archetype : ''
          const pname = personalityId
            ? (personalities ?? []).find((p) => p.id === personalityId)?.name ?? personalityId
            : ''

          return (
            <div key={t.id ?? t.name} className="library-card hud-panel">
              <div className="corner tl" /><div className="corner tr" />
              <div className="corner bl" /><div className="corner br" />
              <div className="scenario-card-body">
                <div className="scenario-card-title">{t.name}</div>
                {t.description && <div className="scenario-card-desc">{t.description}</div>}
                <div className="scenario-card-meta">
                  {t.id_prefix && <span className="badge small info">{t.id_prefix}</span>}
                  {pname && <span className="badge small accent" title="Привязанная личность">{pname}</span>}
                </div>
              </div>
              <div className="scenario-card-actions">
                {user?.role === 'admin' && (
                  <>
                    <button
                      className="btn-clipped small"
                      onClick={() => setEditing({
                        ...EMPTY_AGENT_TYPE,
                        id: typeof raw.id === 'string' ? raw.id : undefined,
                        name: typeof raw.name === 'string' ? raw.name : '',
                        description: typeof raw.description === 'string' ? raw.description : '',
                        id_prefix: typeof raw.id_prefix === 'string' ? raw.id_prefix : '',
                        personality_archetype: personalityId || activePersonalityId || '',
                      })}
                      title="Редактировать"
                      type="button"
                    >
                      Ред.
                    </button>
                    {t.id && (
                      <button className="btn-clipped danger small" onClick={() => handleDelete(t.id!)} title="Удалить" type="button">Удалить</button>
                    )}
                  </>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
