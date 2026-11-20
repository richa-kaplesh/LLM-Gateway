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

// ── Experiments ───────────────────────────────────────────────────────────────

export interface ExperimentRecord {
  name: string
  category: string
  description: string
  metrics: Record<string, number>
  timestamp: string
}

export async function fetchExperiments(): Promise<ExperimentRecord[]> {
  const res = await fetch(`${API_BASE}/experiments`)

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(detail || `Request failed with status ${res.status}`)
  }

  const data = await res.json()
  if (!Array.isArray(data)) return []
  return data as ExperimentRecord[]
}

// ── Live gateway status (/status) ──────────────────────────────────────────────

export type BreakerState = 'closed' | 'open' | 'half_open'

export type Verdict = 'ready' | 'cooling' | 'exhausted' | 'breaker_open' | 'bad_key'

export interface Meter {
  limit: number
  used: number
  remaining: number
  utilisation_pct: number
  full_in_s?: number
  next_free_in_s?: number | null
}

export interface DeploymentStatus {
  id: string
  provider: string
  model: string
  tier: string
  verdict: Verdict
  ready_in_s: number | null
  meters: Partial<Record<'rpm' | 'tpm' | 'rpd' | 'tpd', Meter>>
  cooldown: { cooldown_remaining_s: number; bad_key: boolean }
  breaker: {
    group: string
    state: BreakerState
    consecutive_failures: number
    paused_for_seconds: number
    retry_in_seconds: number
  }
}

export interface GatewayStatus {
  deployments_total: number
  deployments_ready: number
  by_verdict: Partial<Record<Verdict, number>>
  soonest_ready_in_s: number | null
  deployments: DeploymentStatus[]
}

export async function fetchStatus(): Promise<GatewayStatus> {
  const res = await fetch(`${API_BASE}/status`)

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(detail || `Request failed with status ${res.status}`)
  }

  return res.json() as Promise<GatewayStatus>
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

// ── Baseline metrics ───────────────────────────────────────────────────────────

export interface LatencyStats {
  n: number
  avg: number | null
  p50: number | null
  p95: number | null
  p99: number | null
}

export interface ByScope {
  scope: string
  requests: number
  hits: number
  hit_rate_pct: number | null
}

export interface ByProvider {
  provider: string
  requests: number
  avg_latency_ms: number | null
  p95_latency_ms: number | null
  avg_cost_usd: number | null
  fallbacks: number
}

export interface ErrorType {
  error_type: string
  count: number
}

export interface BreakerTransition {
  provider: string
  new_state: string
  count: number
}

export interface MetricsSummary {
  window: {
    since: string | null
    until: string | null
    request_count: number
  }
  totals: {
    requests: number
    errors: number
    error_rate_pct: number | null
    fallback_count: number
    fallback_rate_pct: number | null
  }
  error_types: ErrorType[]
  cache: {
    hits: number
    hit_rate_pct: number | null
    by_scope: ByScope[]
  }
  latency_ms: {
    all: LatencyStats
    hit: LatencyStats
    miss: LatencyStats
  }
  by_provider: ByProvider[]
  cost: {
    total_usd: number | null
    avg_miss_usd: number | null
    avg_hit_usd: number | null
    est_saved_usd: number | null
  }
  embed: {
    rows_with_data: number
    avg_latency_ms: number | null
    p95_latency_ms: number | null
    avg_cost_usd: number | null
    share_of_miss_latency_pct: number | null
  }
  tokens: {
    avg_estimated: number | null
    p95_estimated: number | null
    /** real usage reported by the providers (provider calls only) */
    rows_with_usage?: number
    avg_input?: number | null
    avg_output?: number | null
    avg_thought?: number | null
    total_input?: number | null
    total_output?: number | null
    total_cached?: number | null
    /** estimated / actual (input + output): 1.0 is perfect, above 1 means over-estimating */
    estimate_to_actual_ratio?: number | null
  }
  legacy: {
    hit_rows_with_zero_latency: number
  }
  breaker: {
    transitions: BreakerTransition[]
  }
  deployments?: DeploymentMetrics | null
}

export type DeploymentState = 'active' | 'idle' | 'no_traffic' | 'not_live'

export interface DeploymentHistory {
  deployment_id: string
  provider: string
  model: string
  state: DeploymentState
  attempts: number
  provider_calls: number
  successes: number
  local_refusals: number
  share_of_pool_pct: number | null
  errors: Record<string, number>
  error_rate_pct: number | null
  provider_429s: number
  latency_ms: { p50: number | null; p95: number | null }
  tokens: {
    total_input: number | null
    total_output: number | null
    avg_input: number | null
    avg_output: number | null
  }
  last_attempt_at: string | null
}

export interface PoolHistory {
  provider: string
  model: string
  deployments: string[]
  enabled: number
  provider_calls: number
  fair_share_pct: number | null
  imbalance_pct: number | null
  idle: string[]
}

export interface DeploymentMetrics {
  by_deployment: DeploymentHistory[]
  pools: PoolHistory[]
  routing: {
    gave_up: number
    rejected_while_capacity_existed: number
    rejected_while_capacity_existed_pct: number | null
    provider_429s: number
    multi_attempt_requests: number
    multi_attempt_pct: number | null
    avg_attempts: number | null
  }
}

export interface MetricsSnapshot {
  id: number
  label: string
  taken_at: string
  window_since: string | null
  window_until: string | null
  metrics: MetricsSummary
}

export async function fetchMetricsSummary(
  since?: string,
  until?: string
): Promise<MetricsSummary> {
  const params = new URLSearchParams()
  if (since) params.set('since', since)
  if (until) params.set('until', until)
  const qs = params.toString()
  const res = await fetch(`${API_BASE}/metrics/summary${qs ? `?${qs}` : ''}`)

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(detail || `Request failed with status ${res.status}`)
  }

  return res.json() as Promise<MetricsSummary>
}

export async function fetchMetricsSnapshots(): Promise<MetricsSnapshot[]> {
  const res = await fetch(`${API_BASE}/metrics/snapshots`)

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(detail || `Request failed with status ${res.status}`)
  }

  const data = await res.json()
  if (!Array.isArray(data)) return []
  return data as MetricsSnapshot[]
}

export async function saveMetricsSnapshot(
  label: string,
  since?: string,
  until?: string
): Promise<MetricsSnapshot> {
  const res = await fetch(`${API_BASE}/metrics/snapshots`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ label, since, until }),
  })

  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(detail || `Request failed with status ${res.status}`)
  }

  return res.json() as Promise<MetricsSnapshot>
}
