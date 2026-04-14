export interface SimEvent {
  round?: number | null
  event_type: string
  agent_id: string
  payload: Record<string, unknown>
  timestamp: string
  simulated_date?: string
  simulated_time?: string
  simulated_timestamp?: string
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

export interface EnvironmentInformalLink {
  link_id: string
  agent_a_id: string
  agent_b_id: string
  link_type: string
  strength: number
  visibility: string
  pressure: string
  source: string
}

export interface EnvironmentClimate {
  public_mood: string
  oversight_attention: string
  media_pressure: string
  narrative_temperature: string
  active_signals: string[]
}

export interface SimEnvironment {
  active_signals: string[]
  information_climate?: EnvironmentClimate
  informal_links?: EnvironmentInformalLink[]
}

export interface SimMeta {
  scenario: string
  governance: string
  display_name?: string
  scenario_title?: string
  scenario_id?: string | null
  governance_label?: string
  variant?: string | null
  run_name?: string
  ticks_total?: number
  simulated_start_date?: string | null
  simulated_end_date?: string | null
  runtime?: {
    start_date?: string | null
    tick_granularity?: string
    tick_duration_days?: number
  }
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
  display_name?: string
  scenario_title?: string
  governance_label?: string
  scenario_id?: string | null
  variant?: string | null
  size_kb: number
  created_at?: number
  ticks_total?: number
  simulated_start_date?: string | null
  simulated_end_date?: string | null
}
