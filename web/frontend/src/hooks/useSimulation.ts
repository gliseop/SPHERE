import { useCallback, useEffect, useRef, useState } from 'react'
import type { GraphEdge, GraphNode, RunInfo, SimEvent, SimMeta, WsMessage } from '../types'

export type SimMode = 'idle' | 'playback' | 'live'

export interface SimState {
  meta: SimMeta | null
  events: SimEvent[]
  nodes: GraphNode[]
  edges: GraphEdge[]
  currentRound: number
  done: boolean
  error: string | null
}

const INITIAL_STATE: SimState = {
  meta: null,
  events: [],
  nodes: [],
  edges: [],
  currentRound: 0,
  done: false,
  error: null,
}

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
              meta: { scenario: msg.scenario, governance: msg.governance, seed: msg.seed },
            }
          case 'event':
            return {
              ...prev,
              events: [...prev.events, msg.data],
              currentRound: msg.data.round ?? prev.currentRound,
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
      const url = `${proto}//${window.location.host}/ws/playback/${run.name}?speed=${speed}`
      connect(url, 'playback')
    },
    [connect]
  )

  const startLive = useCallback(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${proto}//${window.location.host}/ws/live`
    connect(url, 'live')
  }, [connect])

  useEffect(() => () => disconnect(), [disconnect])

  return { state, mode, startPlayback, startLive, disconnect }
}
