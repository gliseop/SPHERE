import { useMemo, useState, useEffect } from 'react'
import { useSimulation } from './hooks/useSimulation'
import { SimGraph } from './components/SimGraph'
import { Timeline } from './components/RoundScrubber'
import { ActivityFeed } from './components/ActivityFeed'
import { RunSelector } from './components/RunSelector'
import { AgentList } from './components/AgentList'
import { ScenariosView } from './components/ScenariosView'
import { getBool } from './utils/payload'
import type { RunInfo } from './types'
import './styles/hud.css'

export default function App() {
  const { state, mode, startPlayback, startLive, disconnect } = useSimulation()
  const [view, setView] = useState<'monitor' | 'scenarios'>('monitor')
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [speed, setSpeed] = useState(3.0)
  const [focusDay, setFocusDay] = useState<string | null>(null)

  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    return (localStorage.getItem('magistry-theme') as 'light' | 'dark') ?? 'light'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('magistry-theme', theme)
  }, [theme])

  const privateRatio = useMemo(() => {
    if (!state.events.length) return 0
    const msgs = state.events.filter(
      (e) => e.event_type === 'message_sent' || e.event_type === 'message'
    )
    const priv = msgs.filter((e) => getBool(e.payload, 'private')).length
    return (priv / Math.max(1, msgs.length)) * 100
  }, [state.events])

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
            <span className="hud-header-stat-value">{state.events.length}</span>
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
        </div>
      </header>

      {view === 'monitor' && (
        <>
          <div className="app-main">
            <aside className="panel-left" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
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

            <aside className="panel-right">
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
          <ScenariosView onLaunch={() => setView('monitor')} onStartLive={startLive} />
        </div>
      )}
    </div>
  )
}
