import { useMemo, useState, useEffect } from 'react'
import { useSimulation } from './hooks/useSimulation'
import { SimGraph } from './components/SimGraph'
import { EventTimeline } from './components/EventTimeline'
import { AgentPanel } from './components/AgentPanel'
import { RunSelector } from './components/RunSelector'
import type { RunInfo } from './types'
import './styles/hud.css'

export default function App() {
  const { state, mode, startPlayback, startLive, disconnect } = useSimulation()
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [speed, setSpeed] = useState(3.0)

  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    return (localStorage.getItem('magistry-theme') as 'light' | 'dark') ?? 'light'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('magistry-theme', theme)
  }, [theme])

  const privateRatio = useMemo(() => {
    if (!state.events.length) return 0
    const msgs = state.events.filter((e) => e.event_type === 'message_sent')
    const priv = msgs.filter((e) => e.payload.private).length
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
          {state.meta && (
            <span className="hud-header-meta">
              {state.meta.scenario} / {state.meta.governance}
              {state.meta.seed !== null ? ` / seed${state.meta.seed}` : ''}
            </span>
          )}
        </div>

        <div className="hud-header-stats">
          <div className="hud-header-stat">
            <span className="hud-header-stat-label">Раунд</span>
            <span className="hud-header-stat-value accent">{state.currentRound}</span>
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
            style={{ marginLeft: '0.5rem' }}
          >
            {theme === 'light' ? '◐' : '◑'}
          </button>
        </div>
      </header>

      <div className="app-main">
        <aside className="panel-left">
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
        </aside>

        <main style={{ flex: 1, position: 'relative', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <SimGraph
            nodes={state.nodes}
            edges={state.edges}
            events={state.events}
            onNodeClick={setSelectedNode}
            selectedNode={selectedNode}
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
          <AgentPanel
            nodeId={selectedNode}
            nodes={state.nodes}
            edges={state.edges}
            events={state.events}
          />
        </aside>
      </div>

      <EventTimeline
        events={state.events}
        selectedAgent={selectedNode}
      />
    </div>
  )
}
