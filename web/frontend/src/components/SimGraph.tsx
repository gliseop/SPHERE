import ForceGraph2D, { type NodeObject, type LinkObject } from 'react-force-graph-2d'
import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { GraphEdge, GraphNode, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
  onNodeClick?: (nodeId: string) => void
  selectedNode?: string | null
}

function nodeColor(id: string): string {
  if (id.startsWith('off_')) return '#ef4444'
  if (id.startsWith('biz_')) return '#3b82f6'
  if (id.startsWith('aud_')) return '#f97316'
  return '#6b7280'
}

function edgeColor(strength: number, isPrivate: boolean): string {
  // Подозрительные связи (strength >= 3.0) перекрывают приватность:
  // исследователь должен сразу видеть риск, независимо от типа канала.
  if (strength >= SUSPICIOUS_THRESHOLD) return '#ef4444'
  if (isPrivate) return '#a78bfa'
  return '#94a3b8'
}

export function SimGraph({ nodes, edges, events, onNodeClick, selectedNode }: Props) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const graphRef = useRef<any>(null)

  // Настраиваем d3-силы при монтировании: ограничиваем притяжение рёбер
  // чтобы узлы с сильными связями не слипались в одну точку
  useEffect(() => {
    if (!graphRef.current) return
    graphRef.current.d3Force('link')?.strength(0.08)
    graphRef.current.d3Force('charge')?.strength(-150)
    graphRef.current.d3ReheatSimulation()
  }, [])
  const privateEdges = useMemo(() => {
    const set = new Set<string>()
    for (const e of events) {
      if (e.event_type === 'message_sent' && e.payload.private) {
        const from = e.agent_id
        const to = e.payload.to_id as string
        if (from && to) {
          set.add([from, to].sort().join('|'))
        }
      }
    }
    return set
  }, [events])

  const graphData = useMemo(() => ({
    nodes: nodes.map((n) => ({
      id: n.id,
      reputation: n.reputation,
    })),
    links: edges.map((e) => {
      const key = [e.source, e.target].sort().join('|')
      return {
        source: e.source,
        target: e.target,
        strength: e.strength,
        isPrivate: privateEdges.has(key),
      }
    }),
  }), [nodes, edges, privateEdges])

  const nodeCanvasObject = useCallback(
    (node: NodeObject, ctx: CanvasRenderingContext2D, globalScale: number) => {
      const id = node.id as string
      const reputation = (node as NodeObject & { reputation: number }).reputation ?? 10
      const radius = Math.max(4, Math.min(12, reputation * 0.6))
      const isSelected = id === selectedNode

      ctx.beginPath()
      ctx.arc(node.x!, node.y!, radius, 0, 2 * Math.PI)
      ctx.fillStyle = nodeColor(id)
      ctx.fill()

      if (isSelected) {
        ctx.strokeStyle = '#fbbf24'
        ctx.lineWidth = 2
        ctx.stroke()
      }

      const fontSize = Math.max(8, 10 / globalScale)
      ctx.font = `${fontSize}px sans-serif`
      ctx.fillStyle = '#e2e8f0'
      ctx.textAlign = 'center'
      ctx.fillText(id, node.x!, node.y! + radius + fontSize)
    },
    [selectedNode]
  )

  const linkColor = useCallback(
    (link: LinkObject) => {
      const l = link as LinkObject & { strength: number; isPrivate: boolean }
      return edgeColor(l.strength, l.isPrivate)
    },
    []
  )

  const linkWidth = useCallback(
    (link: LinkObject) => {
      const l = link as LinkObject & { strength: number }
      return Math.min(6, 0.5 + l.strength * 0.4)
    },
    []
  )

  const handleNodeClick = useCallback(
    (node: NodeObject) => {
      onNodeClick?.(node.id as string)
    },
    [onNodeClick]
  )

  if (nodes.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
        Данных нет. Выберите прогон или запустите live-мониторинг.
      </div>
    )
  }

  return (
    <ForceGraph2D
      graphData={graphData}
      nodeCanvasObject={nodeCanvasObject}
      nodePointerAreaPaint={(node, color, ctx) => {
        ctx.beginPath()
        ctx.arc(node.x!, node.y!, 12, 0, 2 * Math.PI)
        ctx.fillStyle = color
        ctx.fill()
      }}
      linkColor={linkColor}
      linkWidth={linkWidth}
      nodeCanvasObjectMode={() => 'replace'}
      linkDirectionalParticles={2}
      linkDirectionalParticleSpeed={0.004}
      onNodeClick={handleNodeClick}
      backgroundColor="#0f172a"
      // Увеличиваем силу отталкивания чтобы узлы не слипались
      // даже при очень сильных рёбрах (strength >> SUSPICIOUS_THRESHOLD)
      ref={graphRef}
      d3AlphaDecay={0.02}
      d3VelocityDecay={0.3}
      width={undefined}
      height={undefined}
    />
  )
}
