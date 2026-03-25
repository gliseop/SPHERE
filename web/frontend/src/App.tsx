import { useCallback, useMemo, useState, useEffect, useRef } from 'react'
import { useSimulation } from './hooks/useSimulation'
import { useAuth } from './hooks/useAuth'
import { LoginPage } from './pages/LoginPage'
import { SimGraph } from './components/SimGraph'
import { Timeline } from './components/RoundScrubber'
import { ActivityFeed } from './components/ActivityFeed'
import { RunSelector } from './components/RunSelector'
import { AgentList } from './components/AgentList'
import { ScenarioPanel } from './components/ScenarioPanel'
import { EnvironmentPanel } from './components/EnvironmentPanel'
import { ScenariosView } from './components/ScenariosView'
import { RunsView } from './components/RunsView'
import { AgentTypesView } from './components/AgentTypesView'
import { PersonalitiesView } from './components/PersonalitiesView'
import { APP_NAME } from './constants'
import { getBool } from './utils/payload'
import { apiClient } from './utils/apiClient'
import type { RunInfo } from './types'
import './styles/hud.css'

const MIN_PANEL = 150
const MAX_PANEL = 400
const COLLAPSED_PANEL = 44

function ResizeHandle({ onDrag }: { onDrag: (delta: number) => void }) {
  const dragging = useRef(false)
  const lastX = useRef(0)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    lastX.current = e.clientX

    const onMouseMove = (ev: MouseEvent) => {
      if (!dragging.current) return
      const delta = ev.clientX - lastX.current
      lastX.current = ev.clientX
      onDrag(delta)
    }

    const onMouseUp = () => {
      dragging.current = false
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [onDrag])

  return (
    <div className="resize-handle" onMouseDown={onMouseDown} />
  )
}

export default function App() {
  const { state, mode, startPlayback, startLive, disconnect } = useSimulation()
  const auth = useAuth()
  const [view, setView] = useState<'monitor' | 'scenarios' | 'runs' | 'agentTypes' | 'personalities'>('monitor')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [speed, setSpeed] = useState(3.0)
  const [focusDay, setFocusDay] = useState<string | null>(null)
  const [activeRuns, setActiveRuns] = useState<Array<{
    run_name: string
    pid: number
    status: 'running' | 'finished'
    returncode?: number
    external?: boolean
    stop_supported?: boolean
  }>>([])
  const [rightTab, setRightTab] = useState<'activity' | 'scenario' | 'environment'>('activity')
  const [scenarioConfig, setScenarioConfig] = useState<Record<string, unknown> | null>(null)
  const [currentRunName, setCurrentRunName] = useState<string | null>(null)

  useEffect(() => {
    if (!auth.isAuthenticated) return
    if (view !== 'monitor' && view !== 'runs' && mode !== 'live') return
    function poll() {
      apiClient.get('/api/runs/active')
        .then((r) => r.ok ? r.json() : [])
        .then((data) => setActiveRuns(Array.isArray(data) ? data : []))
        .catch(() => {})
    }
    poll()
    const interval = setInterval(poll, 5_000)
    return () => clearInterval(interval)
  }, [auth.isAuthenticated, view, mode])

  useEffect(() => {
    if (auth.isAuthenticated) return
    disconnect()
    setActiveRuns([])
    setScenarioConfig(null)
    setCurrentRunName(null)
    setSelectedNode(null)
  }, [auth.isAuthenticated, disconnect])

  useEffect(() => {
    const runName = state.meta?.run_name ?? null
    setCurrentRunName(runName)
    if (!runName) {
      setScenarioConfig(null)
      return
    }
    apiClient.get(`/api/run/${encodeURIComponent(runName)}/scenario`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => setScenarioConfig(data))
      .catch(() => setScenarioConfig(null))
  }, [state.meta?.run_name])

  const [leftWidth, setLeftWidth] = useState<number>(() => {
    const stored = localStorage.getItem('sphere-left-w')
    return stored ? Math.max(MIN_PANEL, Math.min(MAX_PANEL, Number(stored))) : 200
  })
  const [rightWidth, setRightWidth] = useState<number>(() => {
    const stored = localStorage.getItem('sphere-right-w')
    return stored ? Math.max(MIN_PANEL, Math.min(MAX_PANEL, Number(stored))) : 260
  })
  const [leftCollapsed, setLeftCollapsed] = useState<boolean>(() => localStorage.getItem('sphere-left-collapsed') === '1')
  const [rightCollapsed, setRightCollapsed] = useState<boolean>(() => localStorage.getItem('sphere-right-collapsed') === '1')

  useEffect(() => { localStorage.setItem('sphere-left-w', String(leftWidth)) }, [leftWidth])
  useEffect(() => { localStorage.setItem('sphere-right-w', String(rightWidth)) }, [rightWidth])
  useEffect(() => { localStorage.setItem('sphere-left-collapsed', leftCollapsed ? '1' : '0') }, [leftCollapsed])
  useEffect(() => { localStorage.setItem('sphere-right-collapsed', rightCollapsed ? '1' : '0') }, [rightCollapsed])

  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const stored = localStorage.getItem('sphere-theme') as 'light' | 'dark' | null
    if (stored) return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('sphere-theme', theme)
  }, [theme])

  const meaningfulEvents = useMemo(
    () => state.events.filter((e) => e.event_type !== 'idle' && e.event_type !== 'reputation_snapshot'),
    [state.events],
  )

  const messageEvents = useMemo(
    () => meaningfulEvents.filter((e) => e.event_type === 'message_sent' || e.event_type === 'message'),
    [meaningfulEvents],
  )

  const privateStats = useMemo(() => {
    const total = messageEvents.length
    const priv = messageEvents.filter((e) => getBool(e.payload, 'private')).length
    const ratio = total ? (priv / total) * 100 : 0
    return { total, priv, ratio }
  }, [messageEvents])

  const lastDateLabel = useMemo(() => {
    const last = state.events[state.events.length - 1]
    if (!last?.timestamp) return '---'
    const ms = Date.parse(last.timestamp)
    if (!Number.isFinite(ms)) return '---'
    return new Date(ms).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' })
  }, [state.events])

  const lastTimeLabel = useMemo(() => {
    const last = state.events[state.events.length - 1]
    if (!last?.timestamp) return '---'
    const ms = Date.parse(last.timestamp)
    if (!Number.isFinite(ms)) return '---'
    return new Date(ms).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  }, [state.events])

  const lastTimestampTitle = useMemo(() => {
    const last = state.events[state.events.length - 1]
    if (!last?.timestamp) return ''
    const ms = Date.parse(last.timestamp)
    if (!Number.isFinite(ms)) return ''
    return new Date(ms).toLocaleString('ru-RU')
  }, [state.events])

  const handleLeftDrag = useCallback((delta: number) => {
    if (leftCollapsed) setLeftCollapsed(false)
    setLeftWidth((w) => Math.max(MIN_PANEL, Math.min(MAX_PANEL, w + delta)))
  }, [leftCollapsed])

  const handleRightDrag = useCallback((delta: number) => {
    if (rightCollapsed) setRightCollapsed(false)
    setRightWidth((w) => Math.max(MIN_PANEL, Math.min(MAX_PANEL, w - delta)))
  }, [rightCollapsed])

  if (!auth.isAuthenticated) {
    return <LoginPage onLogin={auth.login} />
  }

  return (
    <div className="app-root">
      <header className="hud-header">
        <div className="corner tl accent" />
        <div className="corner tr" />
        <div className="corner bl" />
        <div className="corner br accent" />

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span className="hud-header-logo">{APP_NAME}</span>
          <nav className="hud-nav">
            <button
              className={`hud-nav-tab${view === 'monitor' ? ' active' : ''}`}
              onClick={() => setView('monitor')}
            >
              Монитор
            </button>
            <button
              className={`hud-nav-tab${view === 'scenarios' ? ' active' : ''}`}
              onClick={() => setView('scenarios')}
            >
              Сценарии
            </button>
            <button
              className={`hud-nav-tab${view === 'agentTypes' ? ' active' : ''}`}
              onClick={() => setView('agentTypes')}
              title="Типы агентов"
            >
              Типы
            </button>
            <button
              className={`hud-nav-tab${view === 'personalities' ? ' active' : ''}`}
              onClick={() => setView('personalities')}
            >
              Личности
            </button>
            <button
              className={`hud-nav-tab${view === 'runs' ? ' active' : ''}`}
              onClick={() => setView('runs')}
            >
              Прогоны
              {activeRuns.filter((a) => a.status === 'running').length > 0 && (
                <span className="nav-active-badge">
                  {activeRuns.filter((a) => a.status === 'running').length}
                </span>
              )}
            </button>
          </nav>
          {state.meta && (
            <span className="hud-header-meta">
              {state.meta.scenario}
              {state.meta.governance ? ` / ${state.meta.governance}` : ''}
              {state.meta.seed !== null ? ` / seed${state.meta.seed}` : ''}
              {state.meta.variant ? ` / ${state.meta.variant}` : ''}
            </span>
          )}
        </div>

        <div className="hud-header-stats">
          {view === 'monitor' && (
            <>
              <div className="hud-header-stat">
                {typeof state.currentRound === 'number' ? (
                  <span className="hud-header-stat-label" title="Шаг симуляции (раунд). Это не обязательно календарный день.">Шаг</span>
                ) : (
                  <span className="hud-header-stat-label" title="Текущее время симуляции (по последнему событию).">Время</span>
                )}
                <span className="hud-header-stat-value accent">
                  {typeof state.currentRound === 'number' ? state.currentRound : lastTimeLabel}
                </span>
              </div>
              <div className="hud-header-stat">
                <span className="hud-header-stat-label" title="Дата последнего события (симуляционное время, если включено).">Дата</span>
                <span className="hud-header-stat-value" title={lastTimestampTitle}>{lastDateLabel}</span>
              </div>
              <div className="hud-header-stat">
                <span className="hud-header-stat-label">Событий</span>
                <span className="hud-header-stat-value">{meaningfulEvents.length}</span>
              </div>
              <div className="hud-header-stat">
                <span className="hud-header-stat-label">Приватных</span>
                <span
                  className={`hud-header-stat-value ${(privateStats.total >= 5 && privateStats.ratio > 80) ? 'danger' : ''}`}
                  title={privateStats.total ? `${privateStats.priv}/${privateStats.total}` : 'Нет сообщений'}
                >
                  {privateStats.ratio.toFixed(0)}%
                </span>
              </div>
              <div className="hud-header-stat">
                <span className="hud-header-stat-label">Агентов</span>
                <span className="hud-header-stat-value">{state.nodes.length}</span>
              </div>
              <div className="hud-header-stat">
                <span className="hud-header-stat-label">Очереди</span>
                <span className={`hud-header-stat-value${state.environment.queues.length > 0 ? ' accent' : ''}`}>
                  {state.environment.queues.length}
                </span>
              </div>
              <div className="hud-header-stat">
                <span className="hud-header-stat-label">Сигналы</span>
                <span className={`hud-header-stat-value${state.environment.active_signals.length > 0 ? ' danger' : ''}`}>
                  {state.environment.active_signals.length}
                </span>
              </div>

              {state.done && <span className="badge success">✓ Завершено</span>}
              {state.error && <span className="badge danger">⚠ Ошибка</span>}

              {mode !== 'idle' && (
                <button className="btn-clipped danger small" onClick={disconnect}>
                  Стоп
                </button>
              )}
            </>
          )}
          <button
            className="btn-clipped small"
            onClick={() => setTheme(t => t === 'light' ? 'dark' : 'light')}
            title="Переключить тему"
            aria-pressed={theme === 'dark'}
            style={{ marginLeft: '0.5rem' }}
          >
            {theme === 'light' ? '◐' : '◑'}
          </button>
          <span
            className="hud-header-stat-label"
            style={{ marginLeft: '0.75rem', fontSize: '0.6rem', opacity: 0.8 }}
          >
            {auth.user?.username}
          </span>
          <span
            className={`badge small${auth.user?.role === 'admin' ? ' success' : ''}`}
            style={{ marginLeft: '0.25rem' }}
          >
            {auth.user?.role}
          </span>
          <button
            className="btn-clipped small danger"
            onClick={auth.logout}
            title="Выйти"
            style={{ marginLeft: '0.5rem' }}
          >
            ✕
          </button>
        </div>
      </header>

      {view === 'monitor' && (
        <>
          <div className="app-main">
            <aside
              className={`panel-left${leftCollapsed ? ' collapsed' : ''}`}
              style={{ width: leftCollapsed ? COLLAPSED_PANEL : leftWidth, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
            >
              <button
                className="btn-clipped small panel-collapse-btn left"
                onClick={() => setLeftCollapsed((v) => !v)}
                title={leftCollapsed ? 'Развернуть левую панель' : 'Свернуть левую панель'}
              >
                {leftCollapsed ? '▶' : '◀'}
              </button>

              {!leftCollapsed && (
                <>
                  <div className="panel-left-controls">
                    <RunSelector
                      onPlayback={(run: RunInfo, spd: number) => {
                        setSelectedNode(null)
                        startPlayback(run, spd)
                      }}
                      onLive={(runName?: string) => {
                        setSelectedNode(null)
                        startLive(runName)
                      }}
                      speed={speed}
                      onSpeedChange={setSpeed}
                      mode={mode}
                      user={auth.user}
                      activeRuns={activeRuns.filter((a) => a.status === 'running').map((a) => a.run_name)}
                      onGoToRuns={() => setView('runs')}
                    />
                  </div>
                  <div className="panel-left-agents">
                    <AgentList
                      nodes={state.nodes}
                      edges={state.edges}
                      events={state.events}
                      selectedNode={selectedNode}
                      names={state.names}
                      onSelect={(id) => setSelectedNode(id || null)}
                      scenarioConfig={scenarioConfig}
                    />
                  </div>
                </>
              )}
            </aside>

            <ResizeHandle onDrag={handleLeftDrag} />

            <main style={{ flex: 1, position: 'relative', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              <SimGraph
                nodes={state.nodes}
                edges={state.edges}
                events={state.events}
                onNodeClick={setSelectedNode}
                selectedNode={selectedNode}
                names={state.names}
              />
              {state.error && (
                <div className="graph-overlay">
                  <div className="graph-overlay-inner">
                    <div className="badge danger" style={{ marginBottom: '0.5rem' }}>Ошибка</div>
                    <div>{state.error}</div>
                  </div>
                </div>
              )}
            </main>

            <ResizeHandle onDrag={handleRightDrag} />

            <aside className={`panel-right${rightCollapsed ? ' collapsed' : ''}`} style={{ width: rightCollapsed ? COLLAPSED_PANEL : rightWidth }}>
              <button
                className="btn-clipped small panel-collapse-btn right"
                onClick={() => setRightCollapsed((v) => !v)}
                title={rightCollapsed ? 'Развернуть правую панель' : 'Свернуть правую панель'}
              >
                {rightCollapsed ? '◀' : '▶'}
              </button>
              {!rightCollapsed && (
                <>
                  <div style={{
                    display: 'flex',
                    gap: 0,
                    borderBottom: '1px solid var(--border)',
                    background: 'var(--surface-1)',
                    flexShrink: 0,
                  }}>
                    <button
                      onClick={() => setRightTab('activity')}
                      style={{
                        flex: 1,
                        padding: '0.35rem 0.5rem',
                        fontSize: '0.65rem',
                        fontWeight: rightTab === 'activity' ? 600 : 400,
                        color: rightTab === 'activity' ? 'var(--accent)' : 'var(--text-secondary)',
                        background: 'transparent',
                        border: 'none',
                        borderBottom: rightTab === 'activity' ? '2px solid var(--accent)' : '2px solid transparent',
                        cursor: 'pointer',
                        transition: 'color 0.15s, border-color 0.15s',
                      }}
                    >
                      Активность
                    </button>
                    <button
                      onClick={() => setRightTab('scenario')}
                      style={{
                        flex: 1,
                        padding: '0.35rem 0.5rem',
                        fontSize: '0.65rem',
                        fontWeight: rightTab === 'scenario' ? 600 : 400,
                        color: rightTab === 'scenario' ? 'var(--accent)' : 'var(--text-secondary)',
                        background: 'transparent',
                        border: 'none',
                        borderBottom: rightTab === 'scenario' ? '2px solid var(--accent)' : '2px solid transparent',
                        cursor: 'pointer',
                        transition: 'color 0.15s, border-color 0.15s',
                      }}
                    >
                      Сценарий
                    </button>
                    <button
                      onClick={() => setRightTab('environment')}
                      style={{
                        flex: 1,
                        padding: '0.35rem 0.5rem',
                        fontSize: '0.65rem',
                        fontWeight: rightTab === 'environment' ? 600 : 400,
                        color: rightTab === 'environment' ? 'var(--accent)' : 'var(--text-secondary)',
                        background: 'transparent',
                        border: 'none',
                        borderBottom: rightTab === 'environment' ? '2px solid var(--accent)' : '2px solid transparent',
                        cursor: 'pointer',
                        transition: 'color 0.15s, border-color 0.15s',
                      }}
                    >
                      Среда
                    </button>
                  </div>
                  {rightTab === 'activity' && (
                    <ActivityFeed
                      events={state.events}
                      names={state.names}
                      mode={mode}
                      meta={state.meta}
                      selectedAgent={selectedNode}
                      onClearFilter={() => setSelectedNode(null)}
                      focusDay={focusDay}
                      runName={currentRunName}
                    />
                  )}
                  {rightTab === 'scenario' && (
                    <ScenarioPanel runName={currentRunName} />
                  )}
                  {rightTab === 'environment' && (
                    <EnvironmentPanel environment={state.environment} />
                  )}
                </>
              )}
            </aside>
          </div>

          <Timeline
            events={state.events}
            focusDay={focusDay}
            onDayClick={setFocusDay}
          />
        </>
      )}

      {view === 'scenarios' && (
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <ScenariosView
            onLaunch={() => setView('monitor')}
            onGoLive={(runName?: string) => {
              setSelectedNode(null)
              startLive(runName)
              setView('monitor')
            }}
            user={auth.user}
          />
        </div>
      )}

      {view === 'agentTypes' && (
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <AgentTypesView user={auth.user} />
        </div>
      )}

      {view === 'personalities' && (
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <PersonalitiesView user={auth.user} />
        </div>
      )}

      {view === 'runs' && (
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <RunsView
            onPlayback={(run: RunInfo, spd: number) => {
              setSelectedNode(null)
              startPlayback(run, spd)
              setView('monitor')
            }}
            onLive={(runName?: string) => {
              setSelectedNode(null)
              startLive(runName)
              setView('monitor')
            }}
            speed={speed}
            mode={mode}
            user={auth.user}
            activeRuns={activeRuns}
          />
        </div>
      )}
    </div>
  )
}
