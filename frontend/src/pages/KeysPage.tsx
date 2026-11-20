import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Activity,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  CircleSlash,
  Clock,
  Flame,
  KeyRound,
  Layers,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  Repeat,
  ShieldAlert,
  TriangleAlert,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import {
  fetchMetricsSummary,
  fetchStatus,
  type DeploymentHistory,
  type DeploymentMetrics,
  type DeploymentStatus,
  type GatewayStatus,
  type Meter,
  type PoolHistory,
  type Verdict,
} from '@/lib/api'

const LIVE_POLL_MS = 5000

const VERDICTS: Record<Verdict, { label: string; dot: string; badge: string; bar: string; ring: string }> = {
  ready: {
    label: 'Ready',
    dot: 'bg-emerald-500',
    badge: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    bar: 'bg-emerald-500',
    ring: 'ring-emerald-300',
  },
  cooling: {
    label: 'Cooling',
    dot: 'bg-sky-500',
    badge: 'border-sky-200 bg-sky-50 text-sky-700',
    bar: 'bg-sky-500',
    ring: 'ring-sky-300',
  },
  exhausted: {
    label: 'Exhausted',
    dot: 'bg-amber-500',
    badge: 'border-amber-200 bg-amber-50 text-amber-800',
    bar: 'bg-amber-500',
    ring: 'ring-amber-300',
  },
  breaker_open: {
    label: 'Breaker open',
    dot: 'bg-red-500',
    badge: 'border-red-200 bg-red-50 text-red-700',
    bar: 'bg-red-500',
    ring: 'ring-red-300',
  },
  bad_key: {
    label: 'Bad key',
    dot: 'bg-stone-700',
    badge: 'border-stone-300 bg-stone-100 text-stone-800',
    bar: 'bg-stone-700',
    ring: 'ring-stone-400',
  },
}

const VERDICT_ORDER: Verdict[] = ['ready', 'cooling', 'exhausted', 'breaker_open', 'bad_key']

const PROVIDER_TINT: Record<string, string> = {
  groq: 'bg-amber-500',
  openrouter: 'bg-violet-500',
  gemini: 'bg-blue-500',
}

const STATE_STYLE: Record<string, string> = {
  active: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  idle: 'border-amber-200 bg-amber-50 text-amber-800',
  no_traffic: 'border-stone-200 bg-stone-100 text-stone-500',
  not_live: 'border-stone-200 bg-white text-stone-400',
}

const WINDOWS = [
  { key: '1h', label: '1 h', ms: 3_600_000 },
  { key: '24h', label: '24 h', ms: 86_400_000 },
  { key: '7d', label: '7 d', ms: 7 * 86_400_000 },
  { key: 'all', label: 'All', ms: null },
] as const

type WindowKey = (typeof WINDOWS)[number]['key']

type SortKey = 'deployment_id' | 'provider_calls' | 'success_pct' | 'provider_429s' | 'p95' | 'tokens'

function tint(provider: string): string {
  return PROVIDER_TINT[provider] ?? 'bg-stone-400'
}

function fmtSecs(s: number | null | undefined): string {
  if (s == null) return '—'
  if (s <= 0) return 'now'
  if (s < 60) return `${Math.ceil(s)}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.ceil(s % 60)}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

function fmtNum(n: number | null | undefined): string {
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 10_000) return `${(n / 1000).toFixed(0)}k`
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}k`
  return Number.isInteger(n) ? String(n) : n.toFixed(1)
}

function fmtMs(n: number | null | undefined): string {
  if (n == null) return '—'
  return n >= 1000 ? `${(n / 1000).toFixed(2)}s` : `${Math.round(n)}ms`
}

function fmtPct(n: number | null | undefined): string {
  return n == null ? '—' : `${n.toFixed(n % 1 === 0 ? 0 : 1)}%`
}

function fmtAgo(iso: string | null, now: number): string {
  if (!iso) return 'never'
  const s = (now - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function meterColor(pct: number): string {
  if (pct >= 90) return 'bg-red-500'
  if (pct >= 70) return 'bg-amber-500'
  return 'bg-emerald-500'
}

function meterHint(m: Meter): string {
  if (m.used <= 0) return 'untouched'
  if (m.full_in_s != null) return `fully refilled in ${fmtSecs(m.full_in_s)}`
  if (m.next_free_in_s != null) return `next slot frees in ${fmtSecs(m.next_free_in_s)}`
  return ''
}

function maxUtilisation(d: DeploymentStatus): number {
  return Math.max(0, ...Object.values(d.meters).map((m) => m?.utilisation_pct ?? 0))
}

function VerdictBadge({ verdict, pulse = false }: { verdict: Verdict; pulse?: boolean }) {
  const v = VERDICTS[verdict]
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium', v.badge)}>
      <span className="relative flex w-1.5 h-1.5">
        {pulse && <span className={cn('absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping', v.dot)} />}
        <span className={cn('relative inline-flex w-1.5 h-1.5 rounded-full', v.dot)} />
      </span>
      {v.label}
    </span>
  )
}

function MeterBar({ name, meter }: { name: string; meter: Meter }) {
  const pct = Math.min(100, Math.max(0, meter.utilisation_pct))
  return (
    <div className="group/meter" title={`${fmtNum(meter.used)} / ${fmtNum(meter.limit)} used · ${meterHint(meter)}`}>
      <div className="flex items-baseline justify-between text-[11px]">
        <span className="font-medium uppercase tracking-wide text-stone-500">{name}</span>
        <span className="tabular-nums text-stone-400 transition-colors group-hover/meter:text-stone-700">
          <span className="hidden group-hover/meter:inline">{fmtNum(meter.used)} / {fmtNum(meter.limit)}</span>
          <span className="group-hover/meter:hidden">{fmtNum(meter.remaining)} left</span>
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-stone-100">
        <div
          className={cn('h-full rounded-full transition-all duration-700 ease-out', meterColor(pct))}
          style={{ width: `${pct > 0 ? Math.max(pct, 2) : 0}%` }}
        />
      </div>
    </div>
  )
}

function StatTile({
  icon: Icon,
  label,
  value,
  sub,
  tone = 'stone',
  children,
}: {
  icon: typeof Activity
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: 'stone' | 'emerald' | 'amber' | 'red' | 'violet'
  children?: ReactNode
}) {
  const tones = {
    stone: 'bg-stone-100 text-stone-500',
    emerald: 'bg-emerald-50 text-emerald-600',
    amber: 'bg-amber-50 text-amber-700',
    red: 'bg-red-50 text-red-600',
    violet: 'bg-violet-50 text-violet-600',
  }
  return (
    <Card className="p-4 animate-card-in">
      <div className="flex items-center gap-2">
        <span className={cn('flex h-7 w-7 items-center justify-center rounded-lg', tones[tone])}>
          <Icon className="h-3.5 w-3.5" />
        </span>
        <span className="text-[11px] font-medium uppercase tracking-widest text-stone-400">{label}</span>
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight text-stone-900 tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-stone-500">{sub}</div>}
      {children}
    </Card>
  )
}

function DeploymentCard({
  d,
  readyIn,
  history,
  selected,
  onSelect,
  now,
}: {
  d: DeploymentStatus
  readyIn: number | null
  history?: DeploymentHistory
  selected: boolean
  onSelect: () => void
  now: number
}) {
  const v = VERDICTS[d.verdict]
  const meters = (['rpm', 'tpm', 'rpd', 'tpd'] as const).filter((k) => d.meters[k])
  const breakerTrouble = d.breaker.state !== 'closed' || d.breaker.consecutive_failures > 0
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'group relative w-full overflow-hidden rounded-xl border bg-white p-4 text-left shadow-sm transition-all duration-200 animate-card-in',
        'hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none',
        selected ? cn('ring-2 border-transparent', v.ring) : 'border-stone-200',
      )}
    >
      <span className={cn('absolute inset-y-0 left-0 w-1', v.bar)} />
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={cn('h-2 w-2 shrink-0 rounded-full', tint(d.provider))} />
            <span className="truncate text-sm font-semibold text-stone-900">{d.id}</span>
            <span className="rounded border border-stone-200 px-1.5 py-px text-[10px] uppercase tracking-wide text-stone-400">
              {d.tier}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-stone-400" title={d.model}>{d.model}</p>
        </div>
        <VerdictBadge verdict={d.verdict} pulse={d.verdict === 'ready'} />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3">
        {meters.map((k) => (
          <MeterBar key={k} name={k} meter={d.meters[k]!} />
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-stone-100 pt-3 text-xs">
        {d.verdict === 'ready' ? (
          <span className="inline-flex items-center gap-1 text-emerald-700">
            <Activity className="h-3.5 w-3.5" /> Taking traffic
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-stone-600">
            <Clock className="h-3.5 w-3.5" />
            Ready in <span className="font-semibold tabular-nums text-stone-900">{fmtSecs(readyIn)}</span>
          </span>
        )}
        {breakerTrouble && (
          <span className="inline-flex items-center gap-1 text-red-600">
            <ShieldAlert className="h-3.5 w-3.5" />
            {d.breaker.state.replace('_', '-')} · {d.breaker.consecutive_failures} fail
          </span>
        )}
        {d.cooldown.bad_key && (
          <span className="inline-flex items-center gap-1 text-stone-700">
            <CircleSlash className="h-3.5 w-3.5" /> key rejected
          </span>
        )}
        <span className="ml-auto text-stone-400">
          {history ? `last call ${fmtAgo(history.last_attempt_at, now)}` : 'no history'}
        </span>
      </div>

      <div
        className={cn(
          'grid transition-all duration-300 ease-out',
          selected ? 'mt-3 grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
        )}
      >
        <div className="overflow-hidden">
          {history ? (
            <div className="grid grid-cols-4 gap-2 rounded-lg bg-stone-50 p-3 text-center">
              <MiniStat label="calls" value={fmtNum(history.provider_calls)} />
              <MiniStat label="share" value={fmtPct(history.share_of_pool_pct)} />
              <MiniStat label="429s" value={fmtNum(history.provider_429s)} warn={history.provider_429s > 0} />
              <MiniStat label="p95" value={fmtMs(history.latency_ms.p95)} />
            </div>
          ) : (
            <p className="rounded-lg bg-stone-50 p-3 text-center text-xs text-stone-400">
              No calls in the selected history window.
            </p>
          )}
        </div>
      </div>
    </button>
  )
}

function MiniStat({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return (
    <div>
      <div className={cn('text-sm font-semibold tabular-nums', warn ? 'text-red-600' : 'text-stone-900')}>{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-stone-400">{label}</div>
    </div>
  )
}

function PoolCard({
  pool,
  rows,
  selectedId,
  onSelect,
}: {
  pool: PoolHistory
  rows: DeploymentHistory[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const imbalanceTone =
    pool.imbalance_pct == null
      ? 'border-stone-200 bg-stone-50 text-stone-500'
      : pool.imbalance_pct >= 30
        ? 'border-red-200 bg-red-50 text-red-700'
        : pool.imbalance_pct >= 15
          ? 'border-amber-200 bg-amber-50 text-amber-800'
          : 'border-emerald-200 bg-emerald-50 text-emerald-700'
  const selectedRow = rows.find((r) => r.deployment_id === selectedId)
  return (
    <Card className="p-5 animate-card-in">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={cn('h-2.5 w-2.5 rounded-full', tint(pool.provider))} />
            <span className="text-sm font-semibold capitalize text-stone-900">{pool.provider}</span>
          </div>
          <p className="mt-0.5 truncate text-xs text-stone-400">{pool.model}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="rounded-md border border-stone-200 bg-white px-2 py-0.5 text-stone-600 tabular-nums">
            {fmtNum(pool.provider_calls)} calls
          </span>
          <span
            className={cn('rounded-md border px-2 py-0.5 tabular-nums', imbalanceTone)}
            title="Highest share minus lowest share among enabled keys. Needs 20+ calls to be meaningful."
          >
            {pool.imbalance_pct == null ? 'imbalance: too few calls' : `imbalance ${fmtPct(pool.imbalance_pct)}`}
          </span>
        </div>
      </div>

      <div className="mt-4 space-y-2.5">
        {rows.map((r) => {
          const share = r.share_of_pool_pct ?? 0
          const selected = r.deployment_id === selectedId
          return (
            <button
              key={r.deployment_id}
              type="button"
              onClick={() => onSelect(r.deployment_id)}
              className={cn(
                'group w-full rounded-lg px-2 py-1.5 text-left transition-colors',
                selected ? 'bg-amber-50' : 'hover:bg-stone-50',
              )}
            >
              <div className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-2">
                  <span className="font-medium text-stone-800">{r.deployment_id}</span>
                  {r.state !== 'active' && (
                    <span className={cn('rounded border px-1.5 py-px text-[10px]', STATE_STYLE[r.state])}>
                      {r.state.replace('_', ' ')}
                    </span>
                  )}
                </span>
                <span className="tabular-nums text-stone-500">
                  {fmtPct(r.share_of_pool_pct)} · {fmtNum(r.provider_calls)} calls
                </span>
              </div>
              <div className="relative mt-1.5 h-2 rounded-full bg-stone-100">
                <div
                  className={cn('h-full rounded-full transition-all duration-700 ease-out', tint(pool.provider))}
                  style={{ width: `${share > 0 ? Math.max(share, 1.5) : 0}%`, opacity: selected ? 1 : 0.75 }}
                />
                {pool.fair_share_pct != null && (
                  <span
                    className="absolute -top-1 h-4 w-px bg-stone-500"
                    style={{ left: `${pool.fair_share_pct}%` }}
                    title={`fair share ${fmtPct(pool.fair_share_pct)}`}
                  />
                )}
              </div>
            </button>
          )
        })}
      </div>

      {pool.fair_share_pct != null && (
        <p className="mt-3 flex items-center gap-1.5 text-[11px] text-stone-400">
          <span className="inline-block h-3 w-px bg-stone-500" /> fair share {fmtPct(pool.fair_share_pct)}
        </p>
      )}

      {selectedRow && (
        <div className="mt-4 rounded-lg border border-stone-200 bg-stone-50 p-3 animate-card-in">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <MiniStat label="success" value={`${fmtNum(selectedRow.successes)} / ${fmtNum(selectedRow.provider_calls)}`} />
            <MiniStat label="p50 / p95" value={`${fmtMs(selectedRow.latency_ms.p50)} / ${fmtMs(selectedRow.latency_ms.p95)}`} />
            <MiniStat
              label="tokens in / out"
              value={`${fmtNum(selectedRow.tokens.total_input)} / ${fmtNum(selectedRow.tokens.total_output)}`}
            />
            <MiniStat label="local refusals" value={fmtNum(selectedRow.local_refusals)} />
          </div>
          {Object.keys(selectedRow.errors).length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {Object.entries(selectedRow.errors).map(([k, n]) => (
                <span key={k} className="rounded-md border border-red-200 bg-white px-2 py-0.5 text-[11px] text-red-700">
                  {k.replace('_', ' ')} × {n}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

function SortHeader({
  label,
  k,
  sort,
  onSort,
  align = 'right',
}: {
  label: string
  k: SortKey
  sort: { key: SortKey; dir: 'asc' | 'desc' }
  onSort: (k: SortKey) => void
  align?: 'left' | 'right'
}) {
  const active = sort.key === k
  const Icon = !active ? ArrowUpDown : sort.dir === 'asc' ? ArrowUp : ArrowDown
  return (
    <th className={cn('px-3 py-2 font-medium', align === 'right' ? 'text-right' : 'text-left')}>
      <button
        type="button"
        onClick={() => onSort(k)}
        className={cn(
          'inline-flex items-center gap-1 transition-colors hover:text-stone-900',
          active ? 'text-stone-900' : 'text-stone-400',
        )}
      >
        {label}
        <Icon className="h-3 w-3" />
      </button>
    </th>
  )
}

function sortValue(d: DeploymentHistory, k: SortKey): number | string {
  switch (k) {
    case 'deployment_id':
      return d.deployment_id
    case 'provider_calls':
      return d.provider_calls
    case 'success_pct':
      return d.provider_calls ? d.successes / d.provider_calls : -1
    case 'provider_429s':
      return d.provider_429s
    case 'p95':
      return d.latency_ms.p95 ?? -1
    case 'tokens':
      return (d.tokens.total_input ?? 0) + (d.tokens.total_output ?? 0)
  }
}

export function KeysPage() {
  const [live, setLive] = useState<GatewayStatus | null>(null)
  const [liveAt, setLiveAt] = useState(0)
  const [liveError, setLiveError] = useState<string | null>(null)
  const [paused, setPaused] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  const [windowKey, setWindowKey] = useState<WindowKey>('24h')
  const [history, setHistory] = useState<DeploymentMetrics | null | undefined>(undefined)
  const [historyLoading, setHistoryLoading] = useState(true)

  const [verdictFilter, setVerdictFilter] = useState<Verdict | 'all'>('all')
  const [providerFilter, setProviderFilter] = useState<string>('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'provider_calls', dir: 'desc' })

  const loadLive = useCallback(async () => {
    try {
      const s = await fetchStatus()
      setLive(s)
      setLiveAt(Date.now())
      setLiveError(null)
    } catch (err) {
      setLiveError((err as Error).message || 'Gateway unreachable')
    }
  }, [])

  const loadHistory = useCallback(async (key: WindowKey) => {
    setHistoryLoading(true)
    try {
      const w = WINDOWS.find((x) => x.key === key)!
      const since = w.ms == null ? undefined : new Date(Date.now() - w.ms).toISOString()
      const summary = await fetchMetricsSummary(since)
      setHistory(summary.deployments ?? null)
    } catch (err) {
      toast.error((err as Error).message || 'Failed to load history.')
      setHistory(null)
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    loadLive()
  }, [loadLive])

  useEffect(() => {
    loadHistory(windowKey)
  }, [loadHistory, windowKey])

  useEffect(() => {
    if (paused) return
    const poll = () => {
      if (document.visibilityState === 'visible') loadLive()
    }
    const timer = window.setInterval(poll, LIVE_POLL_MS)
    document.addEventListener('visibilitychange', poll)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', poll)
    }
  }, [paused, loadLive])

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])

  const elapsed = liveAt ? (now - liveAt) / 1000 : 0
  const countdown = (s: number | null) => (s == null ? null : Math.max(0, s - elapsed))

  const historyById = useMemo(() => {
    const m = new Map<string, DeploymentHistory>()
    history?.by_deployment.forEach((d) => m.set(d.deployment_id, d))
    return m
  }, [history])

  const providers = useMemo(
    () => [...new Set(live?.deployments.map((d) => d.provider) ?? [])],
    [live],
  )

  const visible = useMemo(
    () =>
      (live?.deployments ?? []).filter(
        (d) =>
          (verdictFilter === 'all' || d.verdict === verdictFilter) &&
          (providerFilter === 'all' || d.provider === providerFilter),
      ),
    [live, verdictFilter, providerFilter],
  )

  const busiest = useMemo(() => {
    if (!live?.deployments.length) return null
    return live.deployments.reduce((a, b) => (maxUtilisation(b) > maxUtilisation(a) ? b : a))
  }, [live])

  const sortedRows = useMemo(() => {
    const rows = [...(history?.by_deployment ?? [])]
    rows.sort((a, b) => {
      const x = sortValue(a, sort.key)
      const y = sortValue(b, sort.key)
      const c = typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number)
      return sort.dir === 'asc' ? c : -c
    })
    return rows
  }, [history, sort])

  const toggleSort = (k: SortKey) =>
    setSort((s) => (s.key === k ? { key: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: k, dir: 'desc' }))

  const toggleSelect = (id: string) => setSelectedId((cur) => (cur === id ? null : id))

  const routing = history?.routing

  return (
    <div className="mx-auto max-w-6xl space-y-8 p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-stone-900">
            <KeyRound className="h-5 w-5 text-amber-700" /> Keys
          </h1>
          <p className="mt-0.5 text-sm text-stone-500">Live capacity per deployment and how traffic was spread</p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs',
              liveError
                ? 'border-red-200 bg-red-50 text-red-700'
                : paused
                  ? 'border-stone-200 bg-white text-stone-500'
                  : 'border-emerald-200 bg-emerald-50 text-emerald-700',
            )}
          >
            <span className="relative flex h-2 w-2">
              {!paused && !liveError && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
              )}
              <span
                className={cn(
                  'relative inline-flex h-2 w-2 rounded-full',
                  liveError ? 'bg-red-500' : paused ? 'bg-stone-400' : 'bg-emerald-500',
                )}
              />
            </span>
            {liveError ? 'Gateway unreachable' : paused ? 'Paused' : 'Live'}
            {liveAt > 0 && <span className="tabular-nums opacity-70">· {Math.floor(elapsed)}s ago</span>}
          </span>
          <Button variant="outline" size="sm" onClick={() => setPaused((p) => !p)} className="gap-1.5">
            {paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
            {paused ? 'Resume' : 'Pause'}
          </Button>
          <Button variant="outline" size="sm" onClick={loadLive} className="gap-1.5">
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {liveError && live && (
        <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
          Showing the last good reading. {liveError}
        </div>
      )}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {!live ? (
          [1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-[118px] rounded-xl" />)
        ) : (
          <>
            <StatTile
              icon={Activity}
              label="Ready"
              tone={live.deployments_ready === 0 ? 'red' : 'emerald'}
              value={
                <span>
                  {live.deployments_ready}
                  <span className="text-base font-normal text-stone-400"> / {live.deployments_total}</span>
                </span>
              }
            >
              <div className="mt-3 flex h-1.5 overflow-hidden rounded-full bg-stone-100">
                {VERDICT_ORDER.map((v) => {
                  const n = live.by_verdict[v] ?? 0
                  return n ? (
                    <div
                      key={v}
                      className={cn('h-full transition-all duration-700', VERDICTS[v].bar)}
                      style={{ width: `${(n / live.deployments_total) * 100}%` }}
                      title={`${VERDICTS[v].label}: ${n}`}
                    />
                  ) : null
                })}
              </div>
            </StatTile>
            <StatTile
              icon={Clock}
              label="Next capacity"
              tone={live.deployments_ready > 0 ? 'stone' : 'amber'}
              value={live.deployments_ready > 0 ? 'now' : fmtSecs(countdown(live.soonest_ready_in_s))}
              sub={live.deployments_ready > 0 ? 'at least one key is free' : 'until the first key frees up'}
            />
            <StatTile
              icon={Flame}
              label="Busiest key"
              tone={busiest && maxUtilisation(busiest) >= 70 ? 'amber' : 'stone'}
              value={busiest ? fmtPct(maxUtilisation(busiest)) : '—'}
              sub={busiest ? `${busiest.id} · tightest limit` : undefined}
            />
            <StatTile
              icon={TriangleAlert}
              label="Provider 429s"
              tone={routing && routing.provider_429s > 0 ? 'red' : 'stone'}
              value={routing ? fmtNum(routing.provider_429s) : '—'}
              sub={`in the last ${WINDOWS.find((w) => w.key === windowKey)!.label}`}
            />
          </>
        )}
      </section>

      <section className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <FilterChip active={verdictFilter === 'all'} onClick={() => setVerdictFilter('all')}>
            All <span className="opacity-60">{live?.deployments_total ?? 0}</span>
          </FilterChip>
          {VERDICT_ORDER.filter((v) => live?.by_verdict[v]).map((v) => (
            <FilterChip key={v} active={verdictFilter === v} onClick={() => setVerdictFilter(v)}>
              <span className={cn('h-1.5 w-1.5 rounded-full', VERDICTS[v].dot)} />
              {VERDICTS[v].label} <span className="opacity-60">{live?.by_verdict[v]}</span>
            </FilterChip>
          ))}
          {providers.length > 1 && <span className="mx-1 h-5 w-px bg-stone-200" />}
          {providers.length > 1 &&
            ['all', ...providers].map((p) => (
              <FilterChip key={p} active={providerFilter === p} onClick={() => setProviderFilter(p)}>
                {p !== 'all' && <span className={cn('h-1.5 w-1.5 rounded-full', tint(p))} />}
                <span className="capitalize">{p === 'all' ? 'Every provider' : p}</span>
              </FilterChip>
            ))}
        </div>

        {!live ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-52 rounded-xl" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <p className="rounded-xl border border-dashed border-stone-300 py-10 text-center text-sm text-stone-400">
            No deployments match this filter.
          </p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {visible.map((d) => (
              <DeploymentCard
                key={d.id}
                d={d}
                readyIn={countdown(d.ready_in_s)}
                history={historyById.get(d.id)}
                selected={selectedId === d.id}
                onSelect={() => toggleSelect(d.id)}
                now={now}
              />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 pt-6">
          <div>
            <h2 className="text-base font-semibold text-stone-900">History</h2>
            <p className="text-xs text-stone-500">From the request and attempt logs</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="inline-flex rounded-lg border border-stone-200 bg-white p-0.5 shadow-sm">
              {WINDOWS.map((w) => (
                <button
                  key={w.key}
                  type="button"
                  onClick={() => setWindowKey(w.key)}
                  className={cn(
                    'rounded-md px-3 py-1 text-xs font-medium transition-all',
                    windowKey === w.key ? 'bg-amber-700 text-white shadow-sm' : 'text-stone-500 hover:text-stone-900',
                  )}
                >
                  {w.label}
                </button>
              ))}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => loadHistory(windowKey)}
              disabled={historyLoading}
              className="gap-1.5"
            >
              {historyLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            </Button>
          </div>
        </div>

        {history === undefined ? (
          <div className="space-y-3">
            <Skeleton className="h-24 rounded-xl" />
            <Skeleton className="h-48 rounded-xl" />
          </div>
        ) : history === null ? (
          <p className="rounded-xl border border-dashed border-stone-300 py-10 text-center text-sm text-stone-400">
            Per-deployment history is not recorded yet.
          </p>
        ) : (
          <div className={cn('space-y-4 transition-opacity', historyLoading && 'opacity-60')}>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatTile
                icon={CircleSlash}
                label="Wasted rejections"
                tone={history.routing.rejected_while_capacity_existed > 0 ? 'red' : 'emerald'}
                value={fmtNum(history.routing.rejected_while_capacity_existed)}
                sub={
                  history.routing.gave_up
                    ? `${fmtPct(history.routing.rejected_while_capacity_existed_pct)} of ${history.routing.gave_up} give-ups had a free key`
                    : 'no request was given up on'
                }
              />
              <StatTile
                icon={Repeat}
                label="Needed failover"
                tone="violet"
                value={fmtPct(history.routing.multi_attempt_pct)}
                sub={`${fmtNum(history.routing.multi_attempt_requests)} requests took 2+ attempts`}
              />
              <StatTile
                icon={Layers}
                label="Avg attempts"
                value={history.routing.avg_attempts?.toFixed(2) ?? '—'}
                sub="per request"
              />
              <StatTile
                icon={Activity}
                label="Provider calls"
                value={fmtNum(history.pools.reduce((n, p) => n + p.provider_calls, 0))}
                sub={`${fmtNum(history.by_deployment.reduce((n, d) => n + d.local_refusals, 0))} refused locally`}
              />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              {history.pools.map((p) => (
                <PoolCard
                  key={`${p.provider}/${p.model}`}
                  pool={p}
                  rows={history.by_deployment.filter((d) => d.provider === p.provider && d.model === p.model)}
                  selectedId={selectedId}
                  onSelect={toggleSelect}
                />
              ))}
            </div>

            <Card className="overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b border-stone-200 bg-stone-50 text-[11px] uppercase tracking-wide">
                    <tr>
                      <SortHeader label="Deployment" k="deployment_id" sort={sort} onSort={toggleSort} align="left" />
                      <th className="px-3 py-2 text-left font-medium text-stone-400">State</th>
                      <SortHeader label="Calls" k="provider_calls" sort={sort} onSort={toggleSort} />
                      <SortHeader label="Success" k="success_pct" sort={sort} onSort={toggleSort} />
                      <SortHeader label="429s" k="provider_429s" sort={sort} onSort={toggleSort} />
                      <SortHeader label="p95" k="p95" sort={sort} onSort={toggleSort} />
                      <SortHeader label="Tokens" k="tokens" sort={sort} onSort={toggleSort} />
                      <th className="px-3 py-2 text-right font-medium text-stone-400">Last call</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRows.map((r) => {
                      const successPct = r.provider_calls ? (r.successes / r.provider_calls) * 100 : null
                      return (
                        <tr
                          key={r.deployment_id}
                          onClick={() => toggleSelect(r.deployment_id)}
                          className={cn(
                            'cursor-pointer border-b border-stone-100 transition-colors last:border-0',
                            selectedId === r.deployment_id ? 'bg-amber-50' : 'hover:bg-stone-50',
                          )}
                        >
                          <td className="px-3 py-2.5">
                            <div className="flex items-center gap-2">
                              <span className={cn('h-2 w-2 rounded-full', tint(r.provider))} />
                              <span className="font-medium text-stone-900">{r.deployment_id}</span>
                            </div>
                          </td>
                          <td className="px-3 py-2.5">
                            <span className={cn('rounded border px-1.5 py-px text-[11px]', STATE_STYLE[r.state])}>
                              {r.state.replace('_', ' ')}
                            </span>
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums">{fmtNum(r.provider_calls)}</td>
                          <td
                            className={cn(
                              'px-3 py-2.5 text-right tabular-nums',
                              successPct != null && successPct < 90 ? 'text-red-600' : 'text-stone-700',
                            )}
                          >
                            {fmtPct(successPct)}
                          </td>
                          <td
                            className={cn(
                              'px-3 py-2.5 text-right tabular-nums',
                              r.provider_429s > 0 ? 'font-medium text-amber-700' : 'text-stone-400',
                            )}
                          >
                            {r.provider_429s}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-stone-700">{fmtMs(r.latency_ms.p95)}</td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-stone-700">
                            {fmtNum((r.tokens.total_input ?? 0) + (r.tokens.total_output ?? 0) || null)}
                          </td>
                          <td className="px-3 py-2.5 text-right text-xs text-stone-400">
                            {fmtAgo(r.last_attempt_at, now)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        )}
      </section>
    </div>
  )
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-all active:scale-95',
        active
          ? 'border-amber-300 bg-amber-50 text-amber-800 shadow-sm'
          : 'border-stone-200 bg-white text-stone-500 hover:border-stone-300 hover:text-stone-800',
      )}
    >
      {children}
    </button>
  )
}
