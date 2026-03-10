import { useCallback, useEffect, useRef, useState } from 'react'
import { getToken } from '../utils/apiClient'
import type { GraphEdge, GraphNode, RunInfo, SimEvent, SimMeta, WsMessage } from '../types'

export type SimMode = 'idle' | 'playback' | 'live'

export interface SimState {
  meta: SimMeta | null
  names: Record<string, string>   // agent_id -> display name
  events: SimEvent[]
  nodes: GraphNode[]
  edges: GraphEdge[]
  currentRound: number | null
  done: boolean
  error: string | null
}

const INITIAL_STATE: SimState = {
  meta: null,
  names: {},
  events: [],
  nodes: [],
  edges: [],
  currentRound: null,
  done: false,
  error: null,
}

const MAX_EVENTS = 10_000
const EVENT_FLUSH_INTERVAL_MS = 60

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
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
        setState((prev) => ({
          ...prev,
          meta: { scenario: msg.scenario, governance: msg.governance, seed: msg.seed, variant: msg.variant ?? null, run_name: msg.run_name, names: msg.names ?? {} },
          names: msg.names ?? {},
        }))
        return
      }
      if (msg.type === 'graph_state') {
        setState((prev) => ({ ...prev, nodes: msg.nodes, edges: msg.edges }))
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

  useEffect(() => () => disconnect(), [disconnect])

  return { state, mode, startPlayback, startLive, disconnect }
}
