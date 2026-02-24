export interface SimEvent {
  round?: number | null
  event_type: string
  agent_id: string
  payload: Record<string, unknown>
  timestamp: string
}

export interface ThreadGroup {
  thread_id: string
  channel: string
  participants: string[]
  messages: SimEvent[]
  private: boolean
}

export interface GraphNode {
  id: string
  reputation: number
}

export interface GraphEdge {
  source: string
  target: string
  strength: number
}

export interface SimMeta {
  scenario: string
  governance: string
  seed: number | null
  names: Record<string, string>   // agent_id -> display name
}

export type WsMessage =
  | ({ type: 'meta' } & SimMeta)
  | { type: 'event'; data: SimEvent }
  | { type: 'graph_state'; nodes: GraphNode[]; edges: GraphEdge[] }
  | { type: 'done' }
  | { type: 'error'; message: string }

export interface RunInfo {
  name: string
  filename: string
  scenario: string
  governance: string
  seed: number | null
  size_kb: number
}
