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
  has_reputation?: boolean
  reputation_frozen?: boolean
  position_title?: string
  next_position_title?: string
  next_position_threshold?: number
}

export interface GraphEdge {
  source: string
  target: string
  strength: number
}

export interface EnvironmentQueue {
  queue_id: string
  backlog: number
  capacity_per_tick: number
  avg_delay_ticks: number
  status: string
  pressure: string
  owner_org_id: string
  zone_id: string
}

export interface SimEnvironment {
  queues: EnvironmentQueue[]
  active_signals: string[]
}

export interface SimMeta {
  scenario: string
  governance: string
  seed: number | null
  variant?: string | null
  run_name?: string
  names: Record<string, string>   // agent_id -> display name
}

export type WsMessage =
  | ({ type: 'meta' } & SimMeta)
  | { type: 'event'; data: SimEvent }
  | { type: 'events'; data: SimEvent[] }
  | { type: 'graph_state'; nodes: GraphNode[]; edges: GraphEdge[]; environment?: SimEnvironment }
  | { type: 'done' }
  | { type: 'ping'; t?: number }
  | { type: 'error'; message: string }

export interface RunInfo {
  name: string
  filename: string
  scenario: string
  governance: string
  seed: number | null
  variant?: string | null
  size_kb: number
  created_at?: number
}
