import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowDown, ArrowRight, ArrowUp, Minus } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { LiveBadge } from '@/components/LiveBadge'
import { UnreachableNotice, WakingNotice } from '@/components/GatewayNotice'
import { useColdStart } from '@/hooks/useColdStart'
import { OVERVIEW } from '@/config/overview'
import { formatCount, formatLatency, formatPercent, formatWait, formatWindow } from '@/lib/format'
import { cn } from '@/lib/utils'
import {
  fetchMetricsSnapshots,
  fetchStatus,
  type DeploymentStatus,
  type GatewayStatus,
  type MetricsSnapshot,
  type MetricsSummary,
  type Verdict,
} from '@/lib/api'

const POLL_MS = 10_000
const MIN_SAMPLE = 30

const STATUS: Record<Verdict, { label: string; dot: string; text: string; bar: string }> = {
  ready: { label: 'Ready', dot: 'bg-emerald-500', text: 'text-emerald-700', bar: 'bg-emerald-500' },
  cooling: { label: 'Cooling down', dot: 'bg-amber-500', text: 'text-amber-700', bar: 'bg-amber-500' },
  exhausted: { label: 'Out for today', dot: 'bg-amber-500', text: 'text-amber-700', bar: 'bg-amber-500' },
  breaker_open: { label: 'Breaker open', dot: 'bg-red-500', text: 'text-red-700', bar: 'bg-red-500' },
  bad_key: { label: 'Bad key', dot: 'bg-red-500', text: 'text-red-700', bar: 'bg-red-500' },
}

interface ProofMetric {
  key: string
  name: string
  pick: (m: MetricsSummary) => number | null | undefined
  format: (v: number) => string
}

const PROOF_METRICS: ProofMetric[] = [
  { key: 'errors', name: 'Gateway error rate', pick: (m) => m.totals.error_rate_pct, format: formatPercent },
  { key: 'p95', name: 'p95 latency', pick: (m) => m.latency_ms.all.p95, format: formatLatency },
  { key: 'fallback', name: 'Fallback rate', pick: (m) => m.totals.fallback_rate_pct, format: formatPercent },
]

function findSnapshot(snaps: MetricsSnapshot[], label: string): MetricsSnapshot | undefined {
  const want = label.trim().toLowerCase()
  return snaps
    .filter((s) => s.label.trim().toLowerCase() === want)
    .sort((a, b) => b.taken_at.localeCompare(a.taken_at))[0]
}

function describeSnapshot(s: MetricsSnapshot): string {
  const since = s.window_since ?? s.metrics.window.since
  const until = s.window_until ?? s.metrics.window.until ?? s.taken_at
  const n = s.metrics.totals.requests
  return `${formatWindow(since, until)}, ${formatCount(n)} request${n === 1 ? '' : 's'}`
}

function DeltaChip({ before, after }: { before: number; after: number }) {
  if (before === after) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-stone-200 bg-stone-50 px-2 py-0.5 text-xs font-medium text-stone-500">
        <Minus className="h-3 w-3" /> no change
      </span>
    )
  }
  const better = after < before
  const change = before === 0 ? null : Math.round((Math.abs(after - before) / before) * 100)
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium',
        better ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700',
      )}
    >
      {better ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />}
      {change == null ? 'up from zero' : `${change}% ${better ? 'lower' : 'higher'}`}
    </span>
  )
}

function ProofCard({ metric, before, after, delay }: { metric: ProofMetric; before: number; after: number; delay: number }) {
  return (
    <Card className="p-5 animate-card-in" style={{ animationDelay: `${delay}ms` }}>
      <p className="text-xs font-medium uppercase tracking-widest text-stone-400">{metric.name}</p>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-stone-900 tabular-nums">{metric.format(after)}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-sm text-stone-400 tabular-nums">was {metric.format(before)}</span>
        <DeltaChip before={before} after={after} />
      </div>
    </Card>
  )
}

function SampleWarning({ n }: { n: number }) {
  return (
    <span
      title={`Only ${n} request${n === 1 ? '' : 's'} in this snapshot; results may not be representative (n < ${MIN_SAMPLE})`}
      className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-50 px-1.5 text-xs font-medium leading-5 text-amber-700"
    >
      <AlertTriangle className="h-3 w-3" />
      n={n}
    </span>
  )
}

function ProofRow({ before, after }: { before: MetricsSnapshot; after: MetricsSnapshot }) {
  const cards = PROOF_METRICS.flatMap((m) => {
    const b = m.pick(before.metrics)
    const a = m.pick(after.metrics)
    return b == null || a == null ? [] : [{ metric: m, before: b, after: a }]
  })
  if (cards.length === 0) return null
  const nBefore = before.metrics.totals.requests
  const nAfter = after.metrics.totals.requests

  return (
    <section className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        {cards.map((c, i) => (
          <ProofCard key={c.metric.key} metric={c.metric} before={c.before} after={c.after} delay={i * 60} />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-stone-500">
        <span>{OVERVIEW.evalNote}</span>
        <span className="hidden text-stone-300 sm:inline">·</span>
        <span className="inline-flex items-center gap-1">
          before: {describeSnapshot(before)}
          {nBefore < MIN_SAMPLE && <SampleWarning n={nBefore} />}
        </span>
        <span className="hidden text-stone-300 sm:inline">·</span>
        <span className="inline-flex items-center gap-1">
          after: {describeSnapshot(after)}
          {nAfter < MIN_SAMPLE && <SampleWarning n={nAfter} />}
        </span>
        <Link
          to="/baseline"
          className="ml-auto inline-flex items-center gap-1 font-medium text-amber-700 hover:text-amber-800 hover:underline underline-offset-2"
        >
          All numbers <ArrowRight className="h-3 w-3" />
        </Link>
      </div>
    </section>
  )
}

function ProofSkeleton() {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-[118px] rounded-xl" />
      ))}
    </div>
  )
}

function DeploymentTile({ d }: { d: DeploymentStatus }) {
  const s = STATUS[d.verdict]
  const daily = d.meters.rpd ?? d.meters.tpd
  const headroom = daily && daily.limit > 0 ? Math.max(0, Math.min(100, (daily.remaining / daily.limit) * 100)) : null

  return (
    <div className="rounded-xl border border-stone-200 bg-white p-3.5 shadow-sm transition-shadow duration-200 hover:shadow-md">
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-sm font-medium text-stone-900" title={`${d.provider} · ${d.model}`}>
          {d.id}
        </p>
        <span className="shrink-0 rounded-md border border-stone-200 bg-stone-50 px-1.5 text-xs leading-5 text-stone-500">
          {d.tier}
        </span>
      </div>
      <p className={cn('mt-1.5 flex items-center gap-1.5 text-xs font-medium', s.text)}>
        <span className={cn('h-1.5 w-1.5 rounded-full', s.dot)} />
        {s.label}
      </p>
      {daily && headroom != null && (
        <div className="mt-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-stone-100">
            <div className={cn('h-full rounded-full transition-all duration-700', s.bar)} style={{ width: `${headroom}%` }} />
          </div>
          <p className="mt-1 text-xs text-stone-400 tabular-nums">
            {formatCount(daily.remaining)} / {formatCount(daily.limit)} {d.meters.rpd ? 'requests' : 'tokens'} left today
          </p>
        </div>
      )}
    </div>
  )
}

function LivePool({ status }: { status: GatewayStatus }) {
  const next = status.deployments_ready > 0 ? 'now' : formatWait(status.soonest_ready_in_s)
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold text-stone-900">Live pool</h2>
          <p className="text-sm text-stone-500 tabular-nums">
            <span className="font-medium text-stone-900">
              {status.deployments_ready} / {status.deployments_total}
            </span>{' '}
            ready · next capacity: {next}
          </p>
        </div>
        <Link
          to="/keys"
          className="inline-flex items-center gap-1 text-xs font-medium text-amber-700 hover:text-amber-800 hover:underline underline-offset-2"
        >
          Open Keys <ArrowRight className="h-3 w-3" />
        </Link>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {status.deployments.map((d) => (
          <DeploymentTile key={d.id} d={d} />
        ))}
      </div>
    </section>
  )
}

function PoolSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-10 w-56 rounded-lg" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className="h-[104px] rounded-xl" />
        ))}
      </div>
    </div>
  )
}

export function OverviewPage() {
  const [status, setStatus] = useState<GatewayStatus | null>(null)
  const [statusFailed, setStatusFailed] = useState(false)
  const [snapshots, setSnapshots] = useState<MetricsSnapshot[] | null | undefined>(undefined)
  const [attempt, setAttempt] = useState(0)

  const loadStatus = useCallback(
    () =>
      fetchStatus().then(
        (s) => {
          setStatus(s)
          setStatusFailed(false)
        },
        () => setStatusFailed(true),
      ),
    [],
  )

  useEffect(() => {
    fetchStatus().then(
      (s) => {
        setStatus(s)
        setStatusFailed(false)
      },
      () => setStatusFailed(true),
    )
    fetchMetricsSnapshots().then(setSnapshots, () => setSnapshots(null))
  }, [attempt])

  useEffect(() => {
    const tick = () => {
      if (!document.hidden) loadStatus()
    }
    const timer = window.setInterval(tick, POLL_MS)
    document.addEventListener('visibilitychange', tick)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [loadStatus])

  const pending = !status && !statusFailed
  const { waking, timedOut } = useColdStart(pending, attempt)
  const unreachable = !status && (statusFailed || timedOut)

  const retry = () => {
    setStatusFailed(false)
    if (snapshots === null) setSnapshots(undefined)
    setAttempt((a) => a + 1)
  }

  const proof = useMemo(() => {
    if (!snapshots) return null
    const before = findSnapshot(snapshots, OVERVIEW.beforeSnapshotLabel)
    const after = findSnapshot(snapshots, OVERVIEW.afterSnapshotLabel)
    return before && after ? { before, after } : null
  }, [snapshots])

  const badge = status && !statusFailed
    ? <LiveBadge tone="live" label="Live" />
    : statusFailed
      ? <LiveBadge tone="error" label="Unreachable" />
      : <LiveBadge tone="idle" label="Connecting" />

  return (
    <div className="mx-auto max-w-6xl space-y-8 p-4 md:p-6">
      <header className="flex flex-col-reverse gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-2xl">
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">LLM Gateway</h1>
          <p className="mt-1.5 text-base leading-relaxed text-stone-500">
            Turns 8 free-tier API keys into one reliable pool: routes every call to the key with the most room and
            fails over in milliseconds.
          </p>
        </div>
        <div className="shrink-0">{badge}</div>
      </header>

      {unreachable ? (
        <UnreachableNotice onRetry={retry} />
      ) : (
        <>
          {waking && <WakingNotice />}
          {snapshots === undefined ? <ProofSkeleton /> : proof && <ProofRow before={proof.before} after={proof.after} />}
          {status ? <LivePool status={status} /> : <PoolSkeleton />}
        </>
      )}
    </div>
  )
}
