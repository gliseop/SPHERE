import { useMemo, useState } from 'react'
import { useSimulation } from './hooks/useSimulation'
import { SimGraph } from './components/SimGraph'
import { EventFeed } from './components/EventFeed'
import { AgentPanel } from './components/AgentPanel'
import { RunSelector } from './components/RunSelector'
import type { RunInfo } from './types'

export default function App() {
  const { state, mode, startPlayback, startLive, disconnect } = useSimulation()
  const [selectedNode, setSelectedNode] = useState<string | null>(null)
  const [speed, setSpeed] = useState(3.0)

  const privateRatio = useMemo(() => {
    if (!state.events.length) return 0
    const msgs = state.events.filter((e) => e.event_type === 'message_sent')
    const priv = msgs.filter((e) => e.payload.private).length
    return (priv / Math.max(1, msgs.length)) * 100
  }, [state.events])

  return (
    <div className="h-screen bg-slate-900 text-white flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-4 px-4 py-2 bg-slate-800 border-b border-slate-700 shrink-0">
        <span className="font-bold text-slate-200">MAGISTRY Graph UI</span>
        {state.meta && (
          <span className="text-slate-400 text-sm">
            {state.meta.scenario} / {state.meta.governance}
            {state.meta.seed !== null ? ` / seed${state.meta.seed}` : ''}
          </span>
        )}
        <div className="ml-auto flex gap-6 text-xs text-slate-400">
          <span>Раунд: <b className="text-white">{state.currentRound}</b></span>
          <span>Событий: <b className="text-white">{state.events.length}</b></span>
          <span>
            Приватных: <b className="text-white">{privateRatio.toFixed(0)}%</b>
          </span>
          <span>Агентов: <b className="text-white">{state.nodes.length}</b></span>
          {state.done && <span className="text-emerald-400">✓ Завершено</span>}
          {state.error && <span className="text-red-400">⚠ {state.error}</span>}
        </div>
        {mode !== 'idle' && (
          <button
            onClick={disconnect}
            className="text-xs px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600"
          >
            Стоп
          </button>
        )}
      </div>

      {/* Main layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left panel */}
        <div className="w-48 shrink-0 border-r border-slate-700 overflow-y-auto">
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

        {/* Center: graph */}
        <div className="flex-1 relative overflow-hidden">
          <SimGraph
            nodes={state.nodes}
            edges={state.edges}
            events={state.events}
            onNodeClick={setSelectedNode}
            selectedNode={selectedNode}
          />
        </div>

        {/* Right panel */}
        <div className="w-64 shrink-0 border-l border-slate-700 flex flex-col overflow-hidden">
          <div className="h-1/2 border-b border-slate-700 overflow-hidden">
            <AgentPanel
              nodeId={selectedNode}
              nodes={state.nodes}
              edges={state.edges}
              events={state.events}
            />
          </div>
          <div className="h-1/2 overflow-hidden">
            <EventFeed
              events={state.events}
              selectedAgent={selectedNode}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
