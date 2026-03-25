import { useEffect, useMemo, useRef, useState } from 'react'
import { apiClient } from '../utils/apiClient'
import type { AuthUser } from '../hooks/useAuth'

type TechniqueValue =
  | 'denial_of_injury'
  | 'denial_of_victim'
  | 'condemnation_of_condemners'
  | 'appeal_to_higher_loyalties'
  | 'denial_of_responsibility'
  | 'everyone_does_it'
  | 'claim_of_entitlement'
  | 'defense_of_necessity'

const TECHNIQUES: Array<{ value: TechniqueValue; label: string }> = [
  { value: 'denial_of_injury', label: 'Отрицание ущерба' },
  { value: 'denial_of_victim', label: 'Отрицание жертвы' },
  { value: 'condemnation_of_condemners', label: 'Осуждение осуждающих' },
  { value: 'appeal_to_higher_loyalties', label: 'Апелляция к высшим ценностям' },
  { value: 'denial_of_responsibility', label: 'Отрицание ответственности' },
  { value: 'everyone_does_it', label: '«Все так делают»' },
  { value: 'claim_of_entitlement', label: 'Претензия на право' },
  { value: 'defense_of_necessity', label: 'Защита необходимостью' },
]

const ACTIVE_PERSONALITY_STORAGE_KEY = 'sphere-active-personality-id'

const DEFAULT_GENERATE_PERSONALITY_SYSTEM_PROMPT = (
  'Ты — эксперт по организационной психологии и криминологии. '
  + 'Пользователь описывает желаемый типаж персонажа для симуляции коррупции в госорганах. '
  + 'Сгенерируй полный психологический профиль: биографию, параметры HEXACO (0-100), '
  + 'тёмную триаду (0-100) и подходящие техники нейтрализации. '
  + 'Верни ТОЛЬКО JSON без пояснений и префиксов. '
  + 'Биография должна опираться на 1–3 реальных прототипа (исторические/публичные личности; предпочтительно умершие). '
  + 'Персонаж при этом остаётся вымышленным: не используй реальные имена в тексте биографии. '
  + 'Прототипы перечисли в поле prototypes (массив строк). '
  + 'Биография должна быть на русском языке, 3-5 абзацев. '
  + 'Параметры должны быть логически согласованы с описанием и биографией.'
)

function defaultGeneratePersonalityUserPrompt(description: string): string {
  return `Описание персонажа:\n${description}`.trim()
}

interface Hexaco {
  honesty_humility: number
  emotionality: number
  extraversion: number
  agreeableness: number
  conscientiousness: number
  openness: number
}

interface DarkTriad {
  narcissism: number
  machiavellianism: number
  psychopathy: number
}

interface InterviewData {
  id: string
  interview: Record<string, string>
  expert_psychologist: string
  expert_economist: string
  protocol_version: string
}

interface Personality {
  id?: string
  name: string
  description?: string
  prototypes?: string[]
  biography: string
  hexaco: Hexaco
  dark_triad: DarkTriad
  neutralization_techniques: TechniqueValue[]
  has_interview?: boolean
}

const EMPTY_PERSONALITY: Personality = {
  name: '',
  description: '',
  prototypes: [],
  biography: '',
  hexaco: {
    honesty_humility: 50,
    emotionality: 50,
    extraversion: 50,
    agreeableness: 50,
    conscientiousness: 50,
    openness: 50,
  },
  dark_triad: {
    narcissism: 30,
    machiavellianism: 30,
    psychopathy: 30,
  },
  neutralization_techniques: [],
}

type Archetype = 'idealist' | 'pragmatist' | 'opportunist' | 'initiator' | 'machiavellist'

function classifyArchetype(p: Personality): Archetype {
  const hh = p.hexaco.honesty_humility
  const con = p.hexaco.conscientiousness
  const agr = p.hexaco.agreeableness
  const narc = p.dark_triad.narcissism
  const mach = p.dark_triad.machiavellianism
  const psyc = p.dark_triad.psychopathy
  const darkMax = Math.max(narc, mach, psyc)

  if (hh >= 80 && con >= 80 && darkMax < 20) return 'idealist'
  if (hh <= 15 && agr <= 15 && mach >= 90 && psyc >= 70) return 'machiavellist'
  if (hh <= 20 && mach >= 80 && narc >= 70) return 'initiator'
  if (hh <= 40 && narc >= 50) return 'opportunist'
  return 'pragmatist'
}

function archetypeLabel(a: Archetype): string {
  if (a === 'idealist') return 'Идеалист'
  if (a === 'machiavellist') return 'Макиавеллист'
  if (a === 'initiator') return 'Инициатор'
  if (a === 'opportunist') return 'Оппортунист'
  return 'Прагматик'
}

function archetypeBadgeClass(a: Archetype): string {
  if (a === 'idealist') return 'success'
  if (a === 'machiavellist') return 'violet'
  if (a === 'initiator') return 'danger'
  if (a === 'opportunist') return 'warning'
  return 'info'
}

function clamp01_100(n: number): number {
  if (!Number.isFinite(n)) return 0
  return Math.max(0, Math.min(100, Math.round(n)))
}

function prototypesToText(prototypes: string[] | undefined): string {
  if (!Array.isArray(prototypes) || prototypes.length === 0) return ''
  return prototypes.join(', ')
}

function parsePrototypes(text: string): string[] {
  return text
    .split(/[,;\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 3)
}

export function PersonalitiesView({ user }: { user: AuthUser | null }) {
  const [items, setItems] = useState<Personality[] | null>(null)
  const [editing, setEditing] = useState<Personality | null>(null)
  const [showJson, setShowJson] = useState(false)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [activePersonalityId, setActivePersonalityId] = useState<string>(() => {
    try {
      return localStorage.getItem(ACTIVE_PERSONALITY_STORAGE_KEY) ?? ''
    } catch {
      return ''
    }
  })
  const [genSystemPrompt, setGenSystemPrompt] = useState(DEFAULT_GENERATE_PERSONALITY_SYSTEM_PROMPT)
  const [genUserPrompt, setGenUserPrompt] = useState(defaultGeneratePersonalityUserPrompt(''))
  const [genSystemDirty, setGenSystemDirty] = useState(false)
  const [genUserDirty, setGenUserDirty] = useState(false)
  const [loadingInterviewId, setLoadingInterviewId] = useState<string | null>(null)
  const [generatingInterviewId, setGeneratingInterviewId] = useState<string | null>(null)
  const [viewingInterviewId, setViewingInterviewId] = useState<string | null>(null)
  const [interviewData, setInterviewData] = useState<InterviewData | null>(null)
  const prevEditingIdRef = useRef<string | null>(null)
  const editingKey = editing ? (editing.id ?? '__new__') : null

  useEffect(() => {
    apiClient.get('/api/personalities')
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setItems(Array.isArray(data) ? data : []))
      .catch(() => setItems([]))
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(ACTIVE_PERSONALITY_STORAGE_KEY, activePersonalityId)
    } catch {
      // ignore
    }
  }, [activePersonalityId])

  useEffect(() => {
    const curId = editing ? (editing.id ?? '__new__') : null
    if (editing && prevEditingIdRef.current !== curId) {
      setGenSystemPrompt(DEFAULT_GENERATE_PERSONALITY_SYSTEM_PROMPT)
      setGenUserPrompt(defaultGeneratePersonalityUserPrompt(editing.description ?? ''))
      setGenSystemDirty(false)
      setGenUserDirty(false)
    }
    prevEditingIdRef.current = curId
  }, [editing])

  useEffect(() => {
    if (!editingKey) return
    if (genUserDirty) return
    setGenUserPrompt(defaultGeneratePersonalityUserPrompt(editing?.description ?? ''))
  }, [editingKey, editing?.description, genUserDirty])

  const archetype = useMemo(() => (editing ? classifyArchetype(editing) : null), [editing])

  async function handleSave() {
    if (!editing) return
    const payload: Personality = {
      ...editing,
      name: editing.name.trim(),
      description: (editing.description || '').trim(),
      prototypes: Array.isArray(editing.prototypes)
        ? editing.prototypes.map((p) => String(p).trim()).filter(Boolean).slice(0, 3)
        : [],
      biography: (editing.biography || '').trim(),
      hexaco: {
        honesty_humility: clamp01_100(editing.hexaco.honesty_humility),
        emotionality: clamp01_100(editing.hexaco.emotionality),
        extraversion: clamp01_100(editing.hexaco.extraversion),
        agreeableness: clamp01_100(editing.hexaco.agreeableness),
        conscientiousness: clamp01_100(editing.hexaco.conscientiousness),
        openness: clamp01_100(editing.hexaco.openness),
      },
      dark_triad: {
        narcissism: clamp01_100(editing.dark_triad.narcissism),
        machiavellianism: clamp01_100(editing.dark_triad.machiavellianism),
        psychopathy: clamp01_100(editing.dark_triad.psychopathy),
      },
      neutralization_techniques: (editing.neutralization_techniques || []).slice(),
    }
    if (!payload.name) return

    setSaving(true)
    try {
      const res = payload.id
        ? await apiClient.put(`/api/personalities/${payload.id}`, payload)
        : await apiClient.post('/api/personalities', payload)
      if (!res.ok) return
      const saved = await res.json().catch(() => null) as Personality | null
      if (!saved) return
      setItems((prev) => {
        const list = Array.isArray(prev) ? prev : []
        return payload.id
          ? list.map((p) => (p.id === saved.id ? saved : p))
          : [saved, ...list]
      })
      setEditing(null)
    } finally {
      setSaving(false)
    }
  }

  async function handleGenerate() {
    if (!editing || !editing.description?.trim()) return
    setGenerating(true)
    try {
      const res = await apiClient.post('/api/ai/generate-personality', {
        description: editing.description.trim(),
        system_prompt: genSystemPrompt.trim(),
        user_prompt: genUserPrompt.trim(),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        window.alert(text || 'Не удалось сгенерировать профиль')
        return
      }
      const data = await res.json().catch(() => null)
      if (!data || typeof data !== 'object') return
      setEditing((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          prototypes: Array.isArray((data as { prototypes?: unknown }).prototypes)
            ? ((data as { prototypes: unknown[] }).prototypes.map((p) => String(p).trim()).filter(Boolean).slice(0, 3))
            : prev.prototypes,
          biography: typeof data.biography === 'string' ? data.biography : prev.biography,
          hexaco: data.hexaco && typeof data.hexaco === 'object'
            ? { ...prev.hexaco, ...data.hexaco }
            : prev.hexaco,
          dark_triad: data.dark_triad && typeof data.dark_triad === 'object'
            ? { ...prev.dark_triad, ...data.dark_triad }
            : prev.dark_triad,
          neutralization_techniques: Array.isArray(data.neutralization_techniques)
            ? data.neutralization_techniques
            : prev.neutralization_techniques,
        }
      })
    } finally {
      setGenerating(false)
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm('Удалить личность? Это действие необратимо.')) return
    await apiClient.delete(`/api/personalities/${id}`)
    setItems((prev) => (Array.isArray(prev) ? prev.filter((p) => p.id !== id) : []))
  }

  function toggleTechnique(value: TechniqueValue) {
    if (!editing) return
    const set = new Set(editing.neutralization_techniques || [])
    if (set.has(value)) set.delete(value)
    else set.add(value)
    setEditing({ ...editing, neutralization_techniques: [...set] })
  }

  async function handleViewInterview(personalityId: string) {
    if (viewingInterviewId === personalityId) {
      setViewingInterviewId(null)
      setInterviewData(null)
      return
    }
    setLoadingInterviewId(personalityId)
    try {
      const res = await apiClient.get(`/api/personalities/${personalityId}/interview`)
      if (!res.ok) {
        window.alert('Не удалось загрузить интервью')
        return
      }
      const data: InterviewData = await res.json()
      setInterviewData(data)
      setViewingInterviewId(personalityId)
    } finally {
      setLoadingInterviewId(null)
    }
  }

  async function handleDeleteInterview(personalityId: string) {
    if (!window.confirm('Удалить интервью? Потребуется перегенерация.')) return
    await apiClient.delete(`/api/personalities/${personalityId}/interview`)
    setItems((prev) =>
      Array.isArray(prev)
        ? prev.map((p) => (p.id === personalityId ? { ...p, has_interview: false } : p))
        : prev,
    )
    if (viewingInterviewId === personalityId) {
      setViewingInterviewId(null)
      setInterviewData(null)
    }
  }

  async function handleGenerateInterview(personalityId: string) {
    setGeneratingInterviewId(personalityId)
    try {
      const res = await apiClient.post(`/api/personalities/${personalityId}/interview/generate`, { role: 'чиновник' })
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        window.alert(text || 'Не удалось сгенерировать интервью')
        return
      }
      const data: InterviewData = await res.json()
      setItems((prev) =>
        Array.isArray(prev)
          ? prev.map((p) => (p.id === personalityId ? { ...p, has_interview: true } : p))
          : prev,
      )
      setInterviewData(data)
      setViewingInterviewId(personalityId)
    } finally {
      setGeneratingInterviewId(null)
    }
  }

  const list = items ?? []

  if (editing) {
    return (
      <div className="library-editor">
        <div className="library-editor-header">
          <span>{editing.id ? 'Редактировать личность' : 'Новая личность'}</span>
          <button className="btn-clipped small" onClick={() => setEditing(null)}>Отмена</button>
        </div>

        <div className="scenarios-form">
          <div className="form-field">
            <label>Название</label>
            <input
              className="hud-input"
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              placeholder="Например: Прагматик-посредник"
            />
          </div>

          <div className="form-field">
            <div className="form-field-header">
              <label>Описание</label>
              {user?.role === 'admin' && (
                <button
                  className="btn-clipped primary small"
                  onClick={handleGenerate}
                  disabled={generating || !editing.description?.trim()}
                  title="Сгенерировать биографию, HEXACO, тёмную триаду и техники нейтрализации по описанию (LLM)"
                >
                  {generating ? '…' : 'Сгенерировать профиль'}
                </button>
              )}
            </div>
            <textarea
              className="hud-input"
              rows={2}
              value={editing.description ?? ''}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              placeholder="Опишите типаж: роль, поведение, мотивация (минимум 5 символов для генерации)"
            />
            {user?.role === 'admin' && (
              <details className="md-details">
                <summary className="md-summary">Промпт генерации (system/user) — можно подправить перед запуском</summary>
                <div className="hud-panel compact" style={{ padding: '0.75rem' }}>
                  <div className="form-field" style={{ margin: 0 }}>
                    <div className="form-field-header">
                      <label>System prompt</label>
                      <button
                        className="btn-clipped small"
                        onClick={() => {
                          setGenSystemPrompt(DEFAULT_GENERATE_PERSONALITY_SYSTEM_PROMPT)
                          setGenUserPrompt(defaultGeneratePersonalityUserPrompt(editing.description ?? ''))
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
                      rows={4}
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

          <div className="form-field">
            <label>Биография (для промпта)</label>
            <textarea
              className="hud-input"
              rows={6}
              value={editing.biography}
              onChange={(e) => setEditing({ ...editing, biography: e.target.value })}
              placeholder="Короткая биография / мотивация / слепые зоны"
            />
            {archetype && (
              <div style={{ marginTop: '0.25rem' }}>
                <span className={`badge small ${archetypeBadgeClass(archetype)}`}>
                  {archetypeLabel(archetype)}
                </span>
              </div>
            )}
          </div>

          <div className="form-field">
            <label>Прототипы (реальные личности, 1–3)</label>
            <input
              className="hud-input"
              value={prototypesToText(editing.prototypes)}
              onChange={(e) => setEditing({ ...editing, prototypes: parsePrototypes(e.target.value) })}
              placeholder="Например: Никколо Макиавелли, Шарль Морис Талейран"
            />
            <div className="text-muted" style={{ fontSize: '0.65rem', marginTop: '0.25rem' }}>
              Используются как вдохновение для биографии; персонаж остаётся вымышленным.
            </div>
          </div>

          <div className="form-field">
            <div className="form-field-header">
              <label>HEXACO (0–100)</label>
              <span className="text-muted" style={{ fontSize: '0.7rem' }}>
                модель личности
              </span>
            </div>
            <div className="text-muted" style={{ fontSize: '0.7rem', lineHeight: 1.45, marginTop: '0.25rem' }}>
              HEXACO — шестифакторная модель личности (0–100). В SPHERE эти значения используются для
              классификации архетипа (бейдж) и для генерации нарративных материалов (биография/интервью),
              которые затем попадают в промпт LLM-агента.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
              {([
                ['honesty_humility', 'Честность-скромность'],
                ['emotionality', 'Эмоциональность'],
                ['extraversion', 'Экстраверсия'],
                ['agreeableness', 'Доброжелательность'],
                ['conscientiousness', 'Добросовестность'],
                ['openness', 'Открытость опыту'],
              ] as Array<[keyof Hexaco, string]>).map(([key, label]) => (
                <div key={key} className="form-field">
                  <label>{label}</label>
                  <input
                    className="hud-input"
                    type="number"
                    min={0}
                    max={100}
                    value={editing.hexaco[key]}
                    onChange={(e) => setEditing({
                      ...editing,
                      hexaco: { ...editing.hexaco, [key]: Number(e.target.value) },
                    })}
                  />
                </div>
              ))}
            </div>
          </div>

          <div className="form-field">
            <label>Тёмная триада (0–100)</label>
            <div className="text-muted" style={{ fontSize: '0.7rem', lineHeight: 1.45, marginTop: '0.25rem' }}>
              «Тёмная триада» — нарциссизм, макиавеллизм и психопатия (0–100). В проекте это часть профиля,
              которая усиливает/ослабляет склонность к манипуляциям и нарушению норм при генерации биографии/интервью.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem' }}>
              {([
                ['narcissism', 'Нарциссизм'],
                ['machiavellianism', 'Макиавеллизм'],
                ['psychopathy', 'Психопатия'],
              ] as Array<[keyof DarkTriad, string]>).map(([key, label]) => (
                <div key={key} className="form-field">
                  <label>{label}</label>
                  <input
                    className="hud-input"
                    type="number"
                    min={0}
                    max={100}
                    value={editing.dark_triad[key]}
                    onChange={(e) => setEditing({
                      ...editing,
                      dark_triad: { ...editing.dark_triad, [key]: Number(e.target.value) },
                    })}
                  />
                </div>
              ))}
            </div>
          </div>

          <div className="form-field">
            <label>Техники нейтрализации</label>
            <div className="text-muted" style={{ fontSize: '0.7rem', lineHeight: 1.45, marginTop: '0.25rem' }}>
              Техники нейтрализации (Sykes &amp; Matza) — типовые «оправдания» нарушений норм. SPHERE передаёт
              выбранные техники в промпт и в модуль рефлексии: в рефлексиях может появляться пометка
              <span style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: '0.64rem',
                background: 'var(--surface-2)',
                border: '1px solid var(--border)',
                padding: '0.05rem 0.25rem',
                borderRadius: '4px',
              }}
              >
                [technique: …]
              </span>
              , которую затем считают метрики.
            </div>
            <div className="hud-panel compact" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
              {TECHNIQUES.map((t) => {
                const checked = editing.neutralization_techniques.includes(t.value)
                return (
                  <label key={t.value} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', fontSize: '0.75rem', textTransform: 'none', letterSpacing: 0 }}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleTechnique(t.value)}
                    />
                    <span>{t.label}</span>
                  </label>
                )
              })}
            </div>
          </div>

          <div className="form-field">
            <button
              className="btn-clipped small"
              onClick={() => setShowJson(!showJson)}
              style={{ marginBottom: '0.5rem', width: 'fit-content' }}
            >
              {showJson ? 'Скрыть JSON' : 'JSON-превью'}
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
        <span>Личности ({items === null ? '…' : list.length})</span>
        {user?.role === 'admin' && (
          <button className="btn-clipped primary" onClick={() => setEditing({ ...EMPTY_PERSONALITY })}>
            + Создать личность
          </button>
        )}
      </div>

      {list.length === 0 && (
        <div className="scenarios-empty">
          <div className="text-muted">{items === null ? 'Загрузка…' : 'Нет личностей'}</div>
          <div style={{ fontSize: '0.7rem', marginTop: '0.5rem', color: 'var(--text-muted)' }}>
            Личность — это набор черт (HEXACO + Тёмная триада) + биография и техники рационализации для промпта.
          </div>
        </div>
      )}

      <div className="library-list">
        {list.map((p) => {
          const a = classifyArchetype(p)
          const isActive = Boolean(p.id) && p.id === activePersonalityId
          return (
            <div key={p.id ?? p.name} className="library-card hud-panel">
              <div className="corner tl" /><div className="corner tr" />
              <div className="corner bl" /><div className="corner br" />
              <div className="scenario-card-body">
                <div className="scenario-card-title">{p.name}</div>
                {p.description && <div className="scenario-card-desc">{p.description}</div>}
                <div className="scenario-card-meta">
                  <span className={`badge small ${archetypeBadgeClass(a)}`}>{archetypeLabel(a)}</span>
                  <span className="badge small">{p.neutralization_techniques?.length ?? 0} техн.</span>
                  {p.has_interview && <span className="badge small success">интервью</span>}
                  {isActive && <span className="badge small accent">выбрана</span>}
                </div>
              </div>
              <div className="scenario-card-actions">
                <button
                  className={`btn-clipped ${isActive ? 'success' : ''} small`}
                  onClick={() => setActivePersonalityId(isActive ? '' : (p.id ?? ''))}
                  disabled={!p.id}
                  title={isActive ? 'Личность выбрана для генерации (нажмите, чтобы снять)' : 'Выбрать личность для генерации'}
                >
                  {isActive ? '✓' : '○'}
                </button>
                {p.id && p.has_interview && (
                  <button
                    className={`btn-clipped small ${viewingInterviewId === p.id ? 'accent' : ''}`}
                    onClick={() => handleViewInterview(p.id!)}
                    disabled={loadingInterviewId === p.id}
                    title={viewingInterviewId === p.id ? 'Скрыть интервью' : 'Просмотреть интервью'}
                  >
                    {loadingInterviewId === p.id ? '...' : '📋'}
                  </button>
                )}
                {user?.role === 'admin' && (
                  <>
                    {p.id && (
                      <button
                        className="btn-clipped small"
                        onClick={() => handleGenerateInterview(p.id!)}
                        disabled={generatingInterviewId === p.id}
                        title={p.has_interview ? 'Перегенерировать интервью' : 'Сгенерировать интервью'}
                      >
                        {generatingInterviewId === p.id ? '...' : '🧠'}
                      </button>
                    )}
                    <button className="btn-clipped small" onClick={() => setEditing({ ...EMPTY_PERSONALITY, ...p })} title="Редактировать">Ред.</button>
                    {p.id && (
                      <button className="btn-clipped danger small" onClick={() => handleDelete(p.id!)} title="Удалить">Удалить</button>
                    )}
                  </>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {viewingInterviewId && interviewData && (
        <div className="hud-panel" style={{ marginTop: '1rem', padding: '1rem' }}>
          <div className="corner tl" /><div className="corner tr" />
          <div className="corner bl" /><div className="corner br" />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>
                Интервью
              {interviewData.protocol_version === 'v2' && (
                <span className="badge small" style={{ marginLeft: '0.5rem' }}>v2 (30 вопросов)</span>
              )}
            </span>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              {user?.role === 'admin' && (
                <button
                  className="btn-clipped small"
                  onClick={() => handleGenerateInterview(viewingInterviewId)}
                  disabled={generatingInterviewId === viewingInterviewId}
                  title="Перегенерировать интервью"
                >
                  {generatingInterviewId === viewingInterviewId ? '...' : 'Обновить'}
                </button>
              )}
              {user?.role === 'admin' && (
                <button
                  className="btn-clipped danger small"
                  onClick={() => handleDeleteInterview(viewingInterviewId)}
                  title="Удалить интервью"
                >
                  Удалить
                </button>
              )}
              <button
                className="btn-clipped small"
                onClick={() => { setViewingInterviewId(null); setInterviewData(null) }}
              >
                Закрыть
              </button>
            </div>
          </div>

          <div style={{ maxHeight: '60vh', overflowY: 'auto' }}>
            {Object.entries(interviewData.interview).map(([question, answer]) => (
              <details key={question} className="md-details" style={{ marginBottom: '0.5rem' }}>
                <summary className="md-summary" style={{ fontSize: '0.75rem' }}>{question}</summary>
                <div style={{ padding: '0.5rem 0.75rem', fontSize: '0.75rem', lineHeight: 1.6, color: 'var(--text-secondary)' }}>
                  {answer}
                </div>
              </details>
            ))}

            {(interviewData.expert_psychologist || interviewData.expert_economist) && (
              <>
                <div style={{ borderTop: '1px solid var(--border)', margin: '0.75rem 0' }} />
                <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem' }}>Экспертные оценки</div>
                {interviewData.expert_psychologist && (
                  <details className="md-details" style={{ marginBottom: '0.5rem' }}>
                    <summary className="md-summary" style={{ fontSize: '0.75rem' }}>Психолог</summary>
                    <div style={{ padding: '0.5rem 0.75rem', fontSize: '0.75rem', lineHeight: 1.6, color: 'var(--text-secondary)' }}>
                      {interviewData.expert_psychologist}
                    </div>
                  </details>
                )}
                {interviewData.expert_economist && (
                  <details className="md-details" style={{ marginBottom: '0.5rem' }}>
                    <summary className="md-summary" style={{ fontSize: '0.75rem' }}>Экономист</summary>
                    <div style={{ padding: '0.5rem 0.75rem', fontSize: '0.75rem', lineHeight: 1.6, color: 'var(--text-secondary)' }}>
                      {interviewData.expert_economist}
                    </div>
                  </details>
                )}
              </>
            )}
          </div>
        </div>
      )}

    </div>
  )
}
