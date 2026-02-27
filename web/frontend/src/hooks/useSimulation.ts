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

export function useSimulation() {
  const [state, setState] = useState<SimState>(INITIAL_STATE)
  const [mode, setMode] = useState<SimMode>('idle')
  const wsRef = useRef<WebSocket | null>(null)

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setMode('idle')
  }, [])

  const connect = useCallback((url: string, newMode: SimMode) => {
    disconnect()
    setState(INITIAL_STATE)
    setMode(newMode)
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onmessage = (evt) => {
      let msg: WsMessage
      try {
        msg = JSON.parse(evt.data)
      } catch {
        setState((prev) => ({ ...prev, error: 'Invalid message from server' }))
        return
      }
      setState((prev) => {
        switch (msg.type) {
          case 'meta':
            return {
              ...prev,
              meta: { scenario: msg.scenario, governance: msg.governance, seed: msg.seed, variant: msg.variant ?? null, run_name: msg.run_name, names: msg.names ?? {} },
              names: msg.names ?? {},
            }
          case 'event': {
            const nextEvents = prev.events.length >= MAX_EVENTS
              ? [...prev.events.slice(-(MAX_EVENTS - 1)), msg.data]
              : [...prev.events, msg.data]
            return {
              ...prev,
              events: nextEvents,
              currentRound: typeof msg.data.round === 'number'
                ? msg.data.round
                : prev.currentRound,
            }
          }
          case 'graph_state':
            return { ...prev, nodes: msg.nodes, edges: msg.edges }
          case 'done':
            return { ...prev, done: true }
          case 'error':
            return { ...prev, error: msg.message }
          default:
            return prev
        }
      })
    }

    ws.onerror = () => setState((prev) => ({ ...prev, error: 'WebSocket error' }))
    ws.onclose = () => {
      if (wsRef.current === ws) setMode('idle')
    }
  }, [disconnect])

  const startPlayback = useCallback(
    (run: RunInfo, speed: number = 2.0) => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const token = getToken()
      const tokenParam = token ? `&token=${encodeURIComponent(token)}` : ''
      const url = `${proto}//${window.location.host}/ws/playback/${run.name}?speed=${speed}${tokenParam}`
      connect(url, 'playback')
    },
    [connect]
  )

  const startLive = useCallback((runName?: string) => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getToken()
    const qs = new URLSearchParams()
    if (token) qs.set('token', token)
    if (runName) qs.set('run_name', runName)
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    const url = `${proto}//${window.location.host}/ws/live${suffix}`
    connect(url, 'live')
  }, [connect])

  useEffect(() => () => disconnect(), [disconnect])

  return { state, mode, startPlayback, startLive, disconnect }
}
