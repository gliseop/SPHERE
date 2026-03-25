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
import { Icon } from './components/Icons'
import { APP_NAME } from './constants'
import { apiClient } from './utils/apiClient'
import type { RunInfo } from './types'
import './styles/hud.css'

const MIN_PANEL = 150
const MAX_LEFT_PANEL = 400
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
  const { state, mode, startPlayback, startLive, openSnapshot, disconnect } = useSimulation()
  const auth = useAuth()
  const [view, setView] = useState<'monitor' | 'scenarios' | 'runs' | 'agentTypes' | 'personalities'>('monitor')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [speed, setSpeed] = useState(3.0)
  const [focusDay, setFocusDay] = useState<string | null>(null)
  const [activeRuns, setActiveRuns] = useState<Array<{
    run_name: string
    display_name?: string
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
    return stored ? Math.max(MIN_PANEL, Math.min(MAX_LEFT_PANEL, Number(stored))) : 200
  })
  const [rightWidth, setRightWidth] = useState<number>(() => {
    const stored = localStorage.getItem('sphere-right-w')
    const maxWidth = Math.max(MIN_PANEL, Math.floor(window.innerWidth * 0.8))
    return stored ? Math.max(MIN_PANEL, Math.min(maxWidth, Number(stored))) : 320
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

  const lastDateLabel = useMemo(() => {
    const last = state.events[state.events.length - 1]
    if (last?.simulated_date) {
      const ms = Date.parse(`${last.simulated_date}T12:00:00`)
      if (Number.isFinite(ms)) {
        return new Date(ms).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' })
      }
      return last.simulated_date
    }
    if (!last?.timestamp) return state.meta?.simulated_end_date ?? '---'
    const ms = Date.parse(last.timestamp)
    if (!Number.isFinite(ms)) return '---'
    return new Date(ms).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' })
  }, [state.events, state.meta?.simulated_end_date])

  const lastTimeLabel = useMemo(() => {
    const last = state.events[state.events.length - 1]
    if (last?.simulated_time) return last.simulated_time
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
    setLeftWidth((w) => Math.max(MIN_PANEL, Math.min(MAX_LEFT_PANEL, w + delta)))
  }, [leftCollapsed])

  const handleRightDrag = useCallback((delta: number) => {
    if (rightCollapsed) setRightCollapsed(false)
    setRightWidth((w) => {
      const maxWidth = Math.max(MIN_PANEL, Math.floor(window.innerWidth * 0.8))
      return Math.max(MIN_PANEL, Math.min(maxWidth, w - delta))
    })
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
              {state.meta.display_name || state.meta.scenario_title || state.meta.run_name}
              {state.meta.governance_label ? ` / ${state.meta.governance_label}` : ''}
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
                <span className="hud-header-stat-value">{state.totalEvents || meaningfulEvents.length}</span>
              </div>

              {state.done && <span className="badge success">Завершено</span>}
              {state.error && <span className="badge danger">Ошибка</span>}

              {(mode === 'live' || mode === 'playback') && (
                <button className="btn-clipped danger small" onClick={disconnect}>
                  <Icon name="stop" size={14} />
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
            <Icon name="theme" size={14} />
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
            <Icon name="close" size={14} />
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
                <Icon name={leftCollapsed ? 'collapseRight' : 'collapseLeft'} size={14} />
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
                      onOpenRun={(runName: string) => {
                        setSelectedNode(null)
                        void openSnapshot(runName)
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
                <Icon name={rightCollapsed ? 'collapseLeft' : 'collapseRight'} size={14} />
              </button>
              {!rightCollapsed && (
                <>
                  <div className="panel-icon-tabs">
                    <button
                      onClick={() => setRightTab('activity')}
                      className={`panel-icon-tab${rightTab === 'activity' ? ' active' : ''}`}
                      title="Активность"
                      aria-label="Активность"
                    >
                      <Icon name="activity" size={16} />
                    </button>
                    <button
                      onClick={() => setRightTab('scenario')}
                      className={`panel-icon-tab${rightTab === 'scenario' ? ' active' : ''}`}
                      title="Сценарий"
                      aria-label="Сценарий"
                    >
                      <Icon name="scenario" size={16} />
                    </button>
                    <button
                      onClick={() => setRightTab('environment')}
                      className={`panel-icon-tab${rightTab === 'environment' ? ' active' : ''}`}
                      title="Среда"
                      aria-label="Среда"
                    >
                      <Icon name="environment" size={16} />
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
            onOpenRun={(runName: string) => {
              setSelectedNode(null)
              void openSnapshot(runName)
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
