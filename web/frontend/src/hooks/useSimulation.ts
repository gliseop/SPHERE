import { useCallback, useEffect, useRef, useState } from 'react'
import { getToken } from '../utils/apiClient'
import type { GraphEdge, GraphNode, RunInfo, SimEnvironment, SimEvent, SimMeta, WsMessage } from '../types'

export type SimMode = 'idle' | 'playback' | 'live' | 'snapshot'

export interface SimState {
  meta: SimMeta | null
  names: Record<string, string>   // agent_id -> display name
  events: SimEvent[]
  nodes: GraphNode[]
  edges: GraphEdge[]
  environment: SimEnvironment
  currentRound: number | null
  done: boolean
  error: string | null
  totalEvents: number
}

const INITIAL_STATE: SimState = {
  meta: null,
  names: {},
  events: [],
  nodes: [],
  edges: [],
  environment: { queues: [], active_signals: [] },
  currentRound: null,
  done: false,
  error: null,
  totalEvents: 0,
}

const MAX_EVENTS = 10_000
const EVENT_FLUSH_INTERVAL_MS = 60

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function normalizeEnvironment(raw: unknown, fallback?: SimEnvironment): SimEnvironment {
  const base = fallback ?? { queues: [], active_signals: [] }
  if (!isRecord(raw)) return base
  const environment = isRecord(raw.environment) ? raw.environment : raw
  const queuesRaw = Array.isArray(environment.operational_queues)
    ? environment.operational_queues
    : Array.isArray(environment.queues)
      ? environment.queues
      : []
  const activeSignalsRaw = Array.isArray(environment.active_signals)
    ? environment.active_signals
    : isRecord(environment.information_climate) && Array.isArray(environment.information_climate.active_signals)
      ? environment.information_climate.active_signals
      : []
  return {
    queues: queuesRaw
      .filter((item): item is Record<string, unknown> => isRecord(item))
      .map((item) => ({
        queue_id: String(item.queue_id ?? ''),
        title: typeof item.title === 'string' ? item.title : undefined,
        backlog: Number(item.backlog ?? 0),
        capacity_per_tick: Number(item.capacity_per_tick ?? 0),
        avg_delay_ticks: Number(item.avg_delay_ticks ?? 0),
        status: String(item.status ?? ''),
        pressure: String(item.pressure ?? ''),
        owner_org_id: String(item.owner_org_id ?? ''),
        zone_id: String(item.zone_id ?? ''),
      })),
    active_signals: activeSignalsRaw.map((item) => String(item)).filter(Boolean),
    information_climate: isRecord(environment.information_climate)
      ? {
        public_mood: String(environment.information_climate.public_mood ?? ''),
        oversight_attention: String(environment.information_climate.oversight_attention ?? ''),
        media_pressure: String(environment.information_climate.media_pressure ?? ''),
        narrative_temperature: String(environment.information_climate.narrative_temperature ?? ''),
        active_signals: Array.isArray(environment.information_climate.active_signals)
          ? environment.information_climate.active_signals.map((item) => String(item)).filter(Boolean)
          : [],
      }
      : base.information_climate,
    informal_links: Array.isArray(environment.informal_links)
      ? environment.informal_links
        .filter((item): item is Record<string, unknown> => isRecord(item))
        .map((item) => ({
          link_id: String(item.link_id ?? ''),
          agent_a_id: String(item.agent_a_id ?? ''),
          agent_b_id: String(item.agent_b_id ?? ''),
          link_type: String(item.link_type ?? ''),
          strength: Number(item.strength ?? 0),
          visibility: String(item.visibility ?? ''),
          pressure: String(item.pressure ?? ''),
          source: String(item.source ?? ''),
        }))
      : base.informal_links,
  }
}

function extractNamesFromEvent(event: SimEvent): Record<string, string> {
  const out: Record<string, string> = {}
  const rawEvent = event as SimEvent & { agent_name?: unknown }
  if (typeof rawEvent.agent_id === 'string' && typeof rawEvent.agent_name === 'string' && rawEvent.agent_name) {
    out[rawEvent.agent_id] = rawEvent.agent_name
  }

  const payload = isRecord(event.payload) ? event.payload : null
  if (!payload) return out

  const toId = typeof payload.to_id === 'string' ? payload.to_id : ''
  const toName = typeof payload.to_name === 'string' ? payload.to_name : ''
  if (toId && toName) {
    out[toId] = toName
  }

  const entityId = typeof payload.entity_id === 'string' ? payload.entity_id : ''
  const kind = typeof payload.kind === 'string' ? payload.kind : ''
  const meta = isRecord(payload.meta) ? payload.meta : null
  const createdName = meta && typeof meta.name === 'string' ? meta.name : ''
  if (kind === 'agent' && entityId && createdName) {
    out[entityId] = createdName
  }

  return out
}

export function useSimulation() {
  const [state, setState] = useState<SimState>(INITIAL_STATE)
  const [mode, setMode] = useState<SimMode>('idle')
  const wsRef = useRef<WebSocket | null>(null)
  const eventBufferRef = useRef<SimEvent[]>([])
  const flushTimerRef = useRef<number | null>(null)

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    if (flushTimerRef.current !== null) {
      window.clearTimeout(flushTimerRef.current)
      flushTimerRef.current = null
    }
    eventBufferRef.current = []
    setMode('idle')
  }, [])

  const connect = useCallback((url: string, newMode: SimMode, token: string) => {
    disconnect()
    setState(INITIAL_STATE)
    setMode(newMode)
    eventBufferRef.current = []
    const ws = new WebSocket(url)
    wsRef.current = ws

    const flushBufferedEvents = () => {
      const chunk = eventBufferRef.current
      if (chunk.length === 0) return
      eventBufferRef.current = []
      setState((prev) => {
        const normalizedChunk = chunk.length > MAX_EVENTS ? chunk.slice(-MAX_EVENTS) : chunk
        const combined = prev.events.length + normalizedChunk.length
        const nextEvents = combined > MAX_EVENTS
          ? [...prev.events.slice(combined - MAX_EVENTS), ...normalizedChunk]
          : [...prev.events, ...normalizedChunk]
        const nextNames = { ...prev.names }
        for (const event of normalizedChunk) {
          Object.assign(nextNames, extractNamesFromEvent(event))
        }
        let nextRound = prev.currentRound
        for (let i = normalizedChunk.length - 1; i >= 0; i--) {
          const r = normalizedChunk[i]?.round
          if (typeof r === 'number') {
            nextRound = r
            break
          }
        }
        return {
          ...prev,
          events: nextEvents,
          names: nextNames,
          meta: prev.meta ? { ...prev.meta, names: nextNames } : prev.meta,
          currentRound: nextRound,
          totalEvents: Math.max(prev.totalEvents, nextEvents.length),
        }
      })
    }

    const scheduleFlush = () => {
      if (flushTimerRef.current !== null) return
      flushTimerRef.current = window.setTimeout(() => {
        flushTimerRef.current = null
        flushBufferedEvents()
      }, EVENT_FLUSH_INTERVAL_MS)
    }

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token }))
    }

    ws.onmessage = (evt) => {
      let msg: WsMessage
      try {
        msg = JSON.parse(evt.data)
      } catch {
        setState((prev) => ({ ...prev, error: 'Invalid message from server' }))
        return
      }
      if (msg.type === 'event') {
        eventBufferRef.current.push(msg.data)
        scheduleFlush()
        return
      }
      if (msg.type === 'events') {
        if (Array.isArray(msg.data) && msg.data.length > 0) {
          eventBufferRef.current.push(...msg.data)
          scheduleFlush()
        }
        return
      }
      if (msg.type === 'done') {
        flushBufferedEvents()
        setState((prev) => ({ ...prev, done: true }))
        return
      }
      if (msg.type === 'error') {
        flushBufferedEvents()
        setState((prev) => ({ ...prev, error: msg.message }))
        return
      }
      if (msg.type === 'meta') {
        const nextMeta = { ...msg, names: msg.names ?? {}, run_name: msg.run_name } as SimMeta
        setState((prev) => ({
          ...prev,
          meta: nextMeta,
          names: msg.names ?? {},
        }))
        return
      }
      if (msg.type === 'graph_state') {
        setState((prev) => ({
          ...prev,
          nodes: msg.nodes,
          edges: msg.edges,
          environment: normalizeEnvironment(msg.environment, prev.environment),
        }))
        return
      }
      // ping / unknown
    }

    ws.onerror = () => setState((prev) => ({ ...prev, error: 'WebSocket error' }))
    ws.onclose = () => {
      flushBufferedEvents()
      if (wsRef.current === ws) setMode('idle')
    }
  }, [disconnect])

  const startPlayback = useCallback(
    (run: RunInfo, speed: number = 2.0) => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const token = getToken()
      if (!token) {
        setState((prev) => ({ ...prev, error: 'Не выполнен вход' }))
        return
      }
      const url = `${proto}//${window.location.host}/ws/playback/${run.name}?speed=${speed}`
      connect(url, 'playback', token)
    },
    [connect]
  )

  const startLive = useCallback((runName?: string) => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getToken()
    if (!token) {
      setState((prev) => ({ ...prev, error: 'Не выполнен вход' }))
      return
    }
    const qs = new URLSearchParams()
    if (runName) qs.set('run_name', runName)
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    const url = `${proto}//${window.location.host}/ws/live${suffix}`
    connect(url, 'live', token)
  }, [connect])

  const openSnapshot = useCallback(async (runName: string, tailLimit: number = 300) => {
    disconnect()
    setState(INITIAL_STATE)
    setMode('snapshot')
    try {
      const token = getToken()
      if (!token) {
        setState((prev) => ({ ...prev, error: 'Не выполнен вход' }))
        setMode('idle')
        return
      }
      const res = await fetch(`/api/run/${encodeURIComponent(runName)}/snapshot?tail_limit=${tailLimit}`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      })
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`)
      }
      const payload = await res.json()
      const events = Array.isArray(payload.events) ? payload.events as SimEvent[] : []
      const names = isRecord(payload.names)
        ? Object.fromEntries(Object.entries(payload.names).map(([k, v]) => [String(k), String(v)]))
        : {}
      const graph = isRecord(payload.graph) ? payload.graph : {}
      const graphEnvironment = isRecord(graph.environment) ? graph.environment : undefined
      setState({
        meta: isRecord(payload.meta)
          ? { ...(payload.meta as SimMeta), names }
          : null,
        names,
        events,
        nodes: Array.isArray(graph.nodes) ? graph.nodes as GraphNode[] : [],
        edges: Array.isArray(graph.edges) ? graph.edges as GraphEdge[] : [],
        environment: normalizeEnvironment(payload.environment, normalizeEnvironment(graphEnvironment)),
        currentRound: typeof payload.current_round === 'number'
          ? payload.current_round
          : (events[events.length - 1]?.round ?? null),
        done: true,
        error: null,
        totalEvents: typeof payload.total_events === 'number' ? payload.total_events : events.length,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Не удалось открыть прогон'
      setState((prev) => ({ ...prev, error: message }))
      setMode('idle')
    }
  }, [disconnect])

  useEffect(() => () => disconnect(), [disconnect])

  return { state, mode, startPlayback, startLive, openSnapshot, disconnect }
}
