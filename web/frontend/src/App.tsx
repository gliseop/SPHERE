import { useCallback, useMemo, useState, useEffect, useRef } from 'react'
import { useSimulation } from './hooks/useSimulation'
import { useAuth } from './hooks/useAuth'
import { LoginPage } from './pages/LoginPage'
import { SimGraph } from './components/SimGraph'
import { Timeline } from './components/RoundScrubber'
import { ActivityFeed } from './components/ActivityFeed'
import { RunSelector } from './components/RunSelector'
import { AgentList } from './components/AgentList'
import { ScenariosView } from './components/ScenariosView'
import { RunsView } from './components/RunsView'
import { getBool } from './utils/payload'
import { apiClient } from './utils/apiClient'
import type { RunInfo } from './types'
import './styles/hud.css'

const MIN_PANEL = 150
const MAX_PANEL = 400

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
  const [view, setView] = useState<'monitor' | 'scenarios' | 'runs'>('monitor')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [speed, setSpeed] = useState(3.0)
  const [focusDay, setFocusDay] = useState<string | null>(null)
  const [activeRuns, setActiveRuns] = useState<Array<{ run_name: string; pid: number; status: 'running' | 'finished'; returncode?: number }>>([])

  useEffect(() => {
    function poll() {
      apiClient.get('/api/runs/active').then((r) => r.json()).then(setActiveRuns).catch(() => {})
    }
    poll()
    const interval = setInterval(poll, 5_000)
    return () => clearInterval(interval)
  }, [])

  const [leftWidth, setLeftWidth] = useState<number>(() => {
    const stored = localStorage.getItem('magistry-left-w')
    return stored ? Math.max(MIN_PANEL, Math.min(MAX_PANEL, Number(stored))) : 200
  })
  const [rightWidth, setRightWidth] = useState<number>(() => {
    const stored = localStorage.getItem('magistry-right-w')
    return stored ? Math.max(MIN_PANEL, Math.min(MAX_PANEL, Number(stored))) : 260
  })

  useEffect(() => { localStorage.setItem('magistry-left-w', String(leftWidth)) }, [leftWidth])
  useEffect(() => { localStorage.setItem('magistry-right-w', String(rightWidth)) }, [rightWidth])

  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const stored = localStorage.getItem('magistry-theme') as 'light' | 'dark' | null
    if (stored) return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('magistry-theme', theme)
  }, [theme])

  const meaningfulEvents = useMemo(
    () => state.events.filter((e) => e.event_type !== 'idle'),
    [state.events],
  )

  const privateRatio = useMemo(() => {
    if (!meaningfulEvents.length) return 0
    const msgs = meaningfulEvents.filter(
      (e) => e.event_type === 'message_sent' || e.event_type === 'message'
    )
    const priv = msgs.filter((e) => getBool(e.payload, 'private')).length
    return (priv / Math.max(1, msgs.length)) * 100
  }, [meaningfulEvents])

  const handleLeftDrag = useCallback((delta: number) => {
    setLeftWidth((w) => Math.max(MIN_PANEL, Math.min(MAX_PANEL, w + delta)))
  }, [])

  const handleRightDrag = useCallback((delta: number) => {
    setRightWidth((w) => Math.max(MIN_PANEL, Math.min(MAX_PANEL, w - delta)))
  }, [])

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
          <span className="hud-header-logo">MAGISTRY</span>
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
              {state.meta.scenario} / {state.meta.governance}
              {state.meta.seed !== null ? ` / seed${state.meta.seed}` : ''}
            </span>
          )}
        </div>

        <div className="hud-header-stats">
          <div className="hud-header-stat">
            <span className="hud-header-stat-label">
              {typeof state.currentRound === 'number' ? 'Раунд' : 'День'}
            </span>
            <span className="hud-header-stat-value accent">
              {typeof state.currentRound === 'number'
                ? state.currentRound
                : (() => {
                    const last = state.events[state.events.length - 1]
                    if (!last?.timestamp) return '---'
                    const ms = Date.parse(last.timestamp)
                    if (!Number.isFinite(ms)) return '---'
                    return new Date(ms).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
                  })()}
            </span>
          </div>
          <div className="hud-header-stat">
            <span className="hud-header-stat-label">Событий</span>
            <span className="hud-header-stat-value">{meaningfulEvents.length}</span>
          </div>
          <div className="hud-header-stat">
            <span className="hud-header-stat-label">Приватных</span>
            <span className={`hud-header-stat-value ${privateRatio > 50 ? 'danger' : ''}`}>
              {privateRatio.toFixed(0)}%
            </span>
          </div>
          <div className="hud-header-stat">
            <span className="hud-header-stat-label">Агентов</span>
            <span className="hud-header-stat-value">{state.nodes.length}</span>
          </div>

          {state.done && <span className="badge success">✓ Завершено</span>}
          {state.error && <span className="badge danger">⚠ Ошибка</span>}

          {mode !== 'idle' && (
            <button className="btn-clipped danger small" onClick={disconnect}>
              Стоп
            </button>
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
            <aside className="panel-left" style={{ width: leftWidth, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div className="panel-left-controls">
                <RunSelector
                  onPlayback={(run: RunInfo, spd: number) => {
                    setSelectedNode(null)
                    startPlayback(run, spd)
                  }}
                  onLive={() => {
                    setSelectedNode(null)
                    startLive()
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
                />
              </div>
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

            <aside className="panel-right" style={{ width: rightWidth }}>
              <ActivityFeed
                events={state.events}
                names={state.names}
                selectedAgent={selectedNode}
                onClearFilter={() => setSelectedNode(null)}
                focusDay={focusDay}
              />
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
          <ScenariosView onLaunch={() => setView('monitor')} user={auth.user} />
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
            onLive={() => {
              setSelectedNode(null)
              startLive()
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
