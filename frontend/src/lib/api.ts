const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

// ── Query ─────────────────────────────────────────────────────────────────────

export interface GatewayMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ToolCall {
  id: string
  type: string
  function: {
    name: string
    arguments: string
  }
}

export interface QueryRequest {
  conversation_id: string
  user_id: string
  messages: GatewayMessage[]
  tools?: unknown[]
  tool_choice?: string
  stream?: boolean
}

export interface QueryResponse {
  content: string | null
  tool_calls: ToolCall[] | null
  finish_reason: string
  model_used: string
  cost_usd: number
  latency_ms: number
  cache_hit: boolean
}

export async function postQuery(payload: QueryRequest): Promise<QueryResponse> {
  const res = await fetch(`${API_BASE}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(detail || `Request failed with status ${res.status}`)
  }

  const data = await res.json()

  // Support both direct response shape and nested { response: {...} } shape
  const r = data.response ?? data

  return {
    content: r.content ?? null,
    tool_calls: r.tool_calls ?? null,
    finish_reason: r.finish_reason ?? '',
    model_used: r.model_used ?? data.model_used ?? '',
    cost_usd: r.cost_usd ?? data.cost_usd ?? 0,
    latency_ms: r.latency_ms ?? data.latency_ms ?? 0,
    cache_hit: r.cache_hit ?? data.cache_hit ?? false,
  }
}

// ── Global stats ──────────────────────────────────────────────────────────────

export interface RequestOverTime {
  timestamp: string
  cost_usd: number
}

export interface GlobalStats {
  total_requests: number
  total_cost_usd: number
  total_cost_saved_usd: number
  /** 0–1 fraction */
  cache_hit_rate: number
  groq_requests: number
  gemini_requests: number
  requests_over_time: RequestOverTime[]
}

export async function fetchGlobalStats(): Promise<GlobalStats> {
  const res = await fetch(`${API_BASE}/stats/global`)

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(detail || `Request failed with status ${res.status}`)
  }

  const data = await res.json()

  // Backend returns cache_hit_rate as 0–100 and cost_saved_usd (no "total_" prefix).
  // DashboardPage expects cache_hit_rate as 0–1 and total_cost_saved_usd.
  // If no requests yet the backend returns { message: "no requests yet" }.
  if (data.message) {
    return {
      total_requests: 0,
      total_cost_usd: 0,
      total_cost_saved_usd: 0,
      cache_hit_rate: 0,
      groq_requests: 0,
      gemini_requests: 0,
      requests_over_time: [],
    }
  }

  return {
    total_requests: data.total_requests ?? 0,
    total_cost_usd: data.total_cost_usd ?? 0,
    total_cost_saved_usd: data.cost_saved_usd ?? 0,
    cache_hit_rate: (data.cache_hit_rate ?? 0) / 100,
    groq_requests: data.groq_requests ?? 0,
    gemini_requests: data.gemini_requests ?? 0,
    requests_over_time: data.requests_over_time ?? [],
  }
}

// ── Per-request records ────────────────────────────────────────────────────────

export interface RequestRecord {
  timestamp: string
  cost_usd: number
  latency_ms: number
  model_used: string
  cache_hit: boolean
}

export async function fetchRequests(): Promise<RequestRecord[]> {
  const res = await fetch(`${API_BASE}/stats/requests`)

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(detail || `Request failed with status ${res.status}`)
  }

  const data = await res.json()
  // Backend may return { message: '...' } when no data yet
  if (!Array.isArray(data)) return []
  return data as RequestRecord[]
}
