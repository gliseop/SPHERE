import { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import type { GraphEdge, GraphNode, SimEvent } from '../types'
import { SUSPICIOUS_THRESHOLD } from '../constants'
import { NodeTooltip } from './NodeTooltip'

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  events: SimEvent[]
  onNodeClick?: (nodeId: string) => void
  selectedNode?: string | null
  names?: Record<string, string>
}

interface D3Node extends d3.SimulationNodeDatum {
  id: string
  reputation: number
}

interface D3Link extends d3.SimulationLinkDatum<D3Node> {
  source: string | D3Node
  target: string | D3Node
  strength: number
  isPrivate: boolean
}

function nodeColor(id: string): string {
  if (id.startsWith('off_')) return '#ef4444'
  if (id.startsWith('biz_')) return '#3b82f6'
  if (id.startsWith('aud_')) return '#f97316'
  if (id.startsWith('fam_')) return '#f59e0b'
  if (id.startsWith('soc_')) return '#10b981'
  return '#6b7280'
}

function nodeRadius(reputation: number): number {
  return Math.max(5, Math.min(14, reputation * 0.7))
}

function edgeColor(strength: number, isPrivate: boolean): string {
  if (strength >= SUSPICIOUS_THRESHOLD) return '#ef4444'
  if (isPrivate) return '#a78bfa'
  return '#d3d3d3'
}

function edgeWidth(strength: number): number {
  return Math.min(5, 0.5 + strength * 0.4)
}

export function SimGraph({ nodes, edges, events, onNodeClick, selectedNode, names }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const simRef = useRef<d3.Simulation<D3Node, D3Link> | null>(null)
  const nodesRef = useRef<Map<string, D3Node>>(new Map())
  const lastEventRef = useRef<SimEvent | null>(null)
  const [tooltip, setTooltip] = useState<{ node: D3Node; x: number; y: number } | null>(null)
  const selectedRef = useRef(selectedNode)

  // Строим Set приватных рёбер из событий
  const privateEdges = new Set<string>()
  for (const e of events) {
    if (e.event_type === 'message_sent' && e.payload.private) {
      const from = e.agent_id
      const to = e.payload.to_id as string
      if (from && to) privateEdges.add([from, to].sort().join('|'))
    }
  }

  // Синхронизируем ref selectedNode чтобы tick не захватывал устаревший closure
  useEffect(() => {
    selectedRef.current = selectedNode
    if (!svgRef.current) return
    const svg = d3.select(svgRef.current)
    svg.selectAll<SVGCircleElement, D3Node>('circle.node')
      .attr('stroke', (d) => d.id === selectedNode ? '#f97316' : 'none')
      .attr('stroke-width', (d) => d.id === selectedNode ? 2.5 : 0)
  }, [selectedNode])

  // Flash-анимация активного ребра при message_sent
  useEffect(() => {
    const lastEvent = events[events.length - 1]
    if (!lastEvent || lastEvent === lastEventRef.current) return
    lastEventRef.current = lastEvent

    if (lastEvent.event_type !== 'message_sent') return
    const from = lastEvent.agent_id
    const to = typeof lastEvent.payload.to_id === 'string' ? lastEvent.payload.to_id : ''
    if (!from || !to) return

    const svg = d3.select(svgRef.current)
    const key1 = `${from}|${to}`
    const key2 = `${to}|${from}`
    svg.selectAll<SVGLineElement, D3Link>('line.edge')
      .filter((d) => {
        const s = typeof d.source === 'string' ? d.source : (d.source as D3Node).id
        const t = typeof d.target === 'string' ? d.target : (d.target as D3Node).id
        return `${s}|${t}` === key1 || `${s}|${t}` === key2
      })
      .raise()
      .transition().duration(100)
      .attr('stroke', '#f97316')
      .attr('stroke-width', 4)
      .transition().duration(600)
      .attr('stroke', (d) => edgeColor(d.strength, d.isPrivate))
      .attr('stroke-width', (d) => edgeWidth(d.strength))
  }, [events])

  // Инициализация D3 симуляции при монтировании
  useEffect(() => {
    const container = containerRef.current
    const svgEl = svgRef.current
    if (!container || !svgEl) return

    const { width, height } = container.getBoundingClientRect()

    const svg = d3.select(svgEl)
      .attr('width', width)
      .attr('height', height)

    svg.append('g').attr('class', 'links-group')
    svg.append('g').attr('class', 'nodes-group')
    svg.append('g').attr('class', 'labels-group')

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 4])
      .on('zoom', (event) => {
        svg.select('g.links-group').attr('transform', event.transform)
        svg.select('g.nodes-group').attr('transform', event.transform)
        svg.select('g.labels-group').attr('transform', event.transform)
      })
    svg.call(zoom)

    simRef.current = d3.forceSimulation<D3Node>()
      .force('link', d3.forceLink<D3Node, D3Link>().id((d) => d.id).strength(0.08).distance(80))
      .force('charge', d3.forceManyBody().strength(-180))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(20))
      .alphaDecay(0.02)
      .velocityDecay(0.3)

    simRef.current.on('tick', () => {
      const s = d3.select(svgRef.current)
      s.selectAll<SVGLineElement, D3Link>('line.edge')
        .attr('x1', (d) => (d.source as D3Node).x ?? 0)
        .attr('y1', (d) => (d.source as D3Node).y ?? 0)
        .attr('x2', (d) => (d.target as D3Node).x ?? 0)
        .attr('y2', (d) => (d.target as D3Node).y ?? 0)

      s.selectAll<SVGCircleElement, D3Node>('circle.node')
        .attr('cx', (d) => d.x ?? 0)
        .attr('cy', (d) => d.y ?? 0)

      s.selectAll<SVGTextElement, D3Node>('text.node-label')
        .attr('x', (d) => d.x ?? 0)
        .attr('y', (d) => (d.y ?? 0) + nodeRadius(d.reputation) + 12)
    })

    const ro = new ResizeObserver(() => {
      const { width: w, height: h } = container.getBoundingClientRect()
      svg.attr('width', w).attr('height', h)
      simRef.current?.force('center', d3.forceCenter(w / 2, h / 2))
      simRef.current?.alpha(0.3).restart()
    })
    ro.observe(container)

    return () => {
      ro.disconnect()
      simRef.current?.stop()
      svg.selectAll('*').remove()
    }
  }, [])

  // Обновление данных при изменении nodes/edges
  useEffect(() => {
    const sim = simRef.current
    const svgEl = svgRef.current
    if (!sim || !svgEl) return

    const svg = d3.select(svgEl)

    const newNodesMap = new Map<string, D3Node>()
    for (const n of nodes) {
      const existing = nodesRef.current.get(n.id)
      if (existing) {
        existing.reputation = n.reputation
        newNodesMap.set(n.id, existing)
      } else {
        newNodesMap.set(n.id, { id: n.id, reputation: n.reputation })
      }
    }
    nodesRef.current = newNodesMap
    const d3Nodes = Array.from(newNodesMap.values())

    const d3Links: D3Link[] = edges.map((e) => ({
      source: e.source,
      target: e.target,
      strength: e.strength,
      isPrivate: privateEdges.has([e.source, e.target].sort().join('|')),
    }))

    // === РЁБРА ===
    const linkGroup = svg.select('g.links-group')
    const linkSel = linkGroup
      .selectAll<SVGLineElement, D3Link>('line.edge')
      .data(d3Links, (d) => `${String(d.source)}|${String(d.target)}`)

    linkSel.exit().remove()

    linkSel.enter()
      .append('line')
      .attr('class', 'edge')
      .style('opacity', 0)
      .transition().duration(400)
      .style('opacity', 1)

    linkGroup
      .selectAll<SVGLineElement, D3Link>('line.edge')
      .attr('stroke', (d) => edgeColor(d.strength, d.isPrivate))
      .attr('stroke-width', (d) => edgeWidth(d.strength))
      .attr('stroke-opacity', 0.7)

    // === УЗЛЫ ===
    const nodeGroup = svg.select('g.nodes-group')
    const nodeSel = nodeGroup
      .selectAll<SVGCircleElement, D3Node>('circle.node')
      .data(d3Nodes, (d) => d.id)

    nodeSel.exit().remove()

    nodeSel.enter()
      .append('circle')
      .attr('class', 'node')
      .attr('r', 0)
      .attr('fill', (d) => nodeColor(d.id))
      .attr('stroke', 'none')
      .attr('stroke-width', 2.5)
      .style('cursor', 'pointer')
      .call(
        d3.drag<SVGCircleElement, D3Node>()
          .on('start', (event, d) => {
            if (!event.active) sim.alphaTarget(0.3).restart()
            d.fx = d.x; d.fy = d.y
          })
          .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y })
          .on('end', (event, d) => {
            if (!event.active) sim.alphaTarget(0)
            d.fx = null; d.fy = null
          })
      )
      .on('click', (_event, d) => { onNodeClick?.(d.id) })
      .on('mouseenter', (event, d) => {
        setTooltip({ node: d, x: event.clientX, y: event.clientY })
      })
      .on('mousemove', (event) => {
        setTooltip((prev) => prev ? { ...prev, x: event.clientX, y: event.clientY } : null)
      })
      .on('mouseleave', () => setTooltip(null))
      .transition().duration(350)
      .attr('r', (d) => nodeRadius(d.reputation))

    nodeGroup
      .selectAll<SVGCircleElement, D3Node>('circle.node')
      .attr('fill', (d) => nodeColor(d.id))
      .attr('stroke', (d) => d.id === selectedRef.current ? '#f97316' : 'none')
      .transition().duration(200)
      .attr('r', (d) => nodeRadius(d.reputation))

    // === ЛЕЙБЛЫ ===
    const labelGroup = svg.select('g.labels-group')
    const labelSel = labelGroup
      .selectAll<SVGTextElement, D3Node>('text.node-label')
      .data(d3Nodes, (d) => d.id)

    labelSel.exit().remove()

    labelSel.enter()
      .append('text')
      .attr('class', 'node-label')
      .style('opacity', 0)
      .transition().duration(400)
      .style('opacity', 1)

    labelGroup
      .selectAll<SVGTextElement, D3Node>('text.node-label')
      .text((d) => (names ?? {})[d.id] ?? d.id)
      .attr('text-anchor', 'middle')
      .attr('font-family', "'JetBrains Mono', monospace")
      .attr('font-size', '9px')
      .attr('fill', '#666666')
      .style('pointer-events', 'none')
      .style('user-select', 'none')

    sim.nodes(d3Nodes)
    ;(sim.force('link') as d3.ForceLink<D3Node, D3Link>).links(d3Links)
    sim.alpha(0.3).restart()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, events, names])

  // SVG рендерится всегда — иначе useEffect([], []) срабатывает когда svgRef=null
  // и D3 никогда не инициализируется. Пустое состояние — оверлей поверх SVG.
  return (
    <div ref={containerRef} className="graph-container">
      <svg ref={svgRef} style={{ width: '100%', height: '100%' }} />
      {nodes.length === 0 && (
        <div className="graph-empty" style={{ position: 'absolute', inset: 0 }}>
          <div className="graph-empty-icon">◈</div>
          <div>Нет данных</div>
          <div>Выберите прогон или запустите live-мониторинг</div>
        </div>
      )}
      {tooltip && (
        <NodeTooltip
          node={tooltip.node}
          edges={edges}
          events={events}
          names={names ?? {}}
          x={tooltip.x}
          y={tooltip.y}
        />
      )}
    </div>
  )
}
