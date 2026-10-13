import React, { useEffect, useState, useCallback, useRef } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  LineChart,
  Line,
  ReferenceLine,
} from 'recharts'
import {
  Gauge,
  RefreshCw,
  Loader2,
  AlertTriangle,
  Info,
  Save,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardValue } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import {
  fetchMetricsSummary,
  fetchMetricsSnapshots,
  saveMetricsSnapshot,
  fetchExperiments,
  fetchRequests,
  type MetricsSummary,
  type MetricsSnapshot,
  type ExperimentRecord,
  type RequestRecord,
} from '@/lib/api'

// ── Formatting helpers ────────────────────────────────────────────────────────

function fms(v: number | null | undefined): string {
  if (v == null) return 'not recorded'
  return v.toFixed(1) + ' ms'
}
function fpct(v: number | null | undefined): string {
  if (v == null) return 'not recorded'
  return v.toFixed(1) + '%'
}
function fusd(v: number | null | undefined): string {
  if (v == null) return 'not recorded'
  if (v === 0) return '$0.00'
  const abs = Math.abs(v)
  let decimals = 2
  if (abs < 0.01) decimals = 8
  else if (abs < 0.1) decimals = 6
  else if (abs < 1) decimals = 4
  return '$' + v.toFixed(decimals)
}
function fnum(v: number | null | undefined): string {
  if (v == null) return 'not recorded'
  return v.toLocaleString()
}
function trunc(s: string, max = 20): string {
  return s.length > max ? s.slice(0, max) + '...' : s
}
function formatTs(ts: string | null | undefined): string {
  if (!ts) return '--'
  return new Date(ts).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

// ── Low-sample warning badge ──────────────────────────────────────────────────

function LowN({ n }: { n: number }) {
  if (n >= 30) return null
  return (
    <span
      title={`Only ${n} sample${n !== 1 ? 's' : ''} — results may not be representative (n < 30)`}
      className="ml-1.5 inline-flex items-center gap-0.5 rounded border border-amber-200 bg-amber-50 px-1.5 py-0 text-[10px] font-medium text-amber-700 leading-5"
    >
      <AlertTriangle className="w-2.5 h-2.5" />
      n={n}
    </span>
  )
}

// ── "not recorded" placeholder ────────────────────────────────────────────────

function NR() {
  return <span className="text-stone-300 text-xs">not recorded</span>
}

// ── Table primitives ──────────────────────────────────────────────────────────

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={`px-3 py-2 text-[10px] font-semibold uppercase tracking-widest text-stone-400 whitespace-nowrap ${right ? 'text-right' : 'text-left'}`}>
      {children}
    </th>
  )
}
function Td({ children, right, mono }: { children: React.ReactNode; right?: boolean; mono?: boolean }) {
  return (
    <td className={`px-3 py-2 text-sm text-stone-700 ${right ? 'text-right' : ''} ${mono ? 'tabular-nums' : ''}`}>
      {children}
    </td>
  )
}

// ── Window selector ───────────────────────────────────────────────────────────

type WindowMode = 'all' | '24h' | 'since'
interface WindowState { mode: WindowMode; since: string; until: string }

function WindowSelector({ state, onChange }: { state: WindowState; onChange: (s: WindowState) => void }) {
  const btn = (mode: WindowMode, label: string) => (
    <button
      onClick={() => onChange({ ...state, mode })}
      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-150 ${
        state.mode === mode
          ? 'bg-amber-100 text-amber-800 border border-amber-200'
          : 'text-stone-500 hover:bg-stone-100 hover:text-stone-900'
      }`}
    >
      {label}
    </button>
  )
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex items-center gap-1 rounded-lg border border-stone-200 bg-white p-1 shadow-sm">
        {btn('all', 'All time')}
        {btn('24h', 'Last 24 h')}
        {btn('since', 'Custom')}
      </div>
      {state.mode === 'since' && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs text-stone-400">Since</span>
          <Input type="datetime-local" value={state.since} onChange={e => onChange({ ...state, since: e.target.value })} className="h-8 text-xs w-44" />
          <span className="text-xs text-stone-400">Until</span>
          <Input type="datetime-local" value={state.until} onChange={e => onChange({ ...state, until: e.target.value })} className="h-8 text-xs w-44" />
        </div>
      )}
    </div>
  )
}

// ── Save snapshot dialog ──────────────────────────────────────────────────────

function SaveSnapshotDialog({
  onClose, onSave, since, until,
}: {
  onClose: () => void
  onSave: (label: string) => Promise<void>
  since?: string
  until?: string
}) {
  const [label, setLabel] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSave() {
    if (!label.trim()) { toast.error('Please enter a label.'); return }
    setSaving(true)
    try { await onSave(label.trim()); onClose() }
    finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-xl border border-stone-200 bg-white shadow-xl p-6 mx-4 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-stone-900">Save snapshot</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-stone-100 text-stone-400"><X className="w-4 h-4" /></button>
        </div>
        <p className="text-xs text-stone-500 leading-relaxed">
          Capture the current window as a named snapshot for future comparison.
          {since && <span className="block mt-1">Window: {formatTs(since)}{until ? ` to ${formatTs(until)}` : ' to now'}</span>}
        </p>
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-stone-600">Label</label>
          <Input
            placeholder="e.g. Before cache warm-up"
            value={label}
            onChange={e => setLabel(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSave()}
            autoFocus
          />
        </div>
        <div className="flex gap-2 justify-end pt-1">
          <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={handleSave} disabled={saving || !label.trim()}>
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            Save
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Metric card ───────────────────────────────────────────────────────────────

function MetricCard({ title, value, sub, loading, accent }: {
  title: string; value: string; sub?: React.ReactNode; loading?: boolean; accent?: boolean
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {loading ? <Skeleton className="h-8 w-28 mt-1" /> : (
          <CardValue className={accent ? 'text-amber-700' : undefined}>{value}</CardValue>
        )}
        {!loading && sub && <div className="mt-0.5">{sub}</div>}
      </CardHeader>
    </Card>
  )
}

// ── Latency table ─────────────────────────────────────────────────────────────

function LatencyTable({ data }: { data: MetricsSummary['latency_ms'] }) {
  const rows = [
    { label: 'All', stats: data.all },
    { label: 'Cache hit', stats: data.hit },
    { label: 'Cache miss', stats: data.miss },
  ] as const
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-stone-100">
            <Th>Group</Th><Th right>n</Th><Th right>avg</Th><Th right>p50</Th><Th right>p95</Th><Th right>p99</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ label, stats }) => (
            <tr key={label} className="border-b border-stone-50 hover:bg-stone-50 transition-colors">
              <Td><span className="font-medium text-stone-800">{label}</span><LowN n={stats.n} /></Td>
              <Td right mono>{stats.n.toLocaleString()}</Td>
              <Td right mono>{stats.avg == null ? <NR /> : fms(stats.avg)}</Td>
              <Td right mono>{stats.p50 == null ? <NR /> : fms(stats.p50)}</Td>
              <Td right mono>{stats.p95 == null ? <NR /> : fms(stats.p95)}</Td>
              <Td right mono>{stats.p99 == null ? <NR /> : fms(stats.p99)}</Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Latency bar chart (p50 vs p95, hit vs miss) ───────────────────────────────

function LatencyBarChart({ data }: { data: MetricsSummary['latency_ms'] }) {
  const chartData = [
    { group: 'Cache hit',  p50: data.hit.p50,  p95: data.hit.p95  },
    { group: 'Cache miss', p50: data.miss.p50, p95: data.miss.p95 },
  ].filter(d => d.p50 != null || d.p95 != null)

  if (chartData.length === 0) return <p className="text-xs text-stone-400 text-center py-8">No latency data to chart yet.</p>

  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={chartData} barGap={4} barSize={28} margin={{ left: 0, right: 8 }}>
        <CartesianGrid vertical={false} stroke="#F5F5F4" />
        <XAxis dataKey="group" tick={{ fill: '#A8A29E', fontSize: 11 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: '#A8A29E', fontSize: 11 }} axisLine={false} tickLine={false} width={52} tickFormatter={(v: number) => v + 'ms'} />
        <Tooltip
          content={({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number | null; color: string }>; label?: string }) => {
            if (!active || !payload?.length) return null
            return (
              <div className="rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs shadow-lg space-y-1">
                <p className="text-stone-400">{label}</p>
                {payload.map(p => (
                  <p key={p.name} style={{ color: p.color }}>
                    <span className="text-stone-500">{p.name}: </span>
                    <span className="font-medium text-stone-800">{p.value != null ? p.value.toFixed(1) + ' ms' : 'not recorded'}</span>
                  </p>
                ))}
              </div>
            )
          }}
        />
        <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
        <Bar dataKey="p50" name="p50" fill="#D97706" radius={[3, 3, 0, 0]} />
        <Bar dataKey="p95" name="p95" fill="#2563EB" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

// ── Latency over time chart ───────────────────────────────────────────────────

function LatencyTimelineChart({ requests, experiments }: { requests: RequestRecord[]; experiments: ExperimentRecord[] }) {
  const sorted = [...requests].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
  const chartData = sorted.map(r => ({
    ts: new Date(r.timestamp).getTime(),
    latency_ms: r.latency_ms,
    cache_hit: r.cache_hit,
  }))
  const expLines = experiments.map(e => ({
    ts: new Date(e.timestamp).getTime(),
    label: trunc(e.name.replace(/_/g, ' '), 18),
  }))

  if (chartData.length === 0) return <p className="text-xs text-stone-400 text-center py-8">No request history to chart.</p>

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={chartData} margin={{ left: 0, right: 16, bottom: 8 }}>
        <CartesianGrid vertical={false} stroke="#F5F5F4" />
        <XAxis
          dataKey="ts" type="number" scale="time" domain={['dataMin', 'dataMax']}
          tickFormatter={(v: number) => new Date(v).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
          tick={{ fill: '#A8A29E', fontSize: 10 }} axisLine={false} tickLine={false} tickCount={6}
        />
        <YAxis
          dataKey="latency_ms" tick={{ fill: '#A8A29E', fontSize: 11 }} axisLine={false} tickLine={false}
          width={52} tickFormatter={(v: number) => v + 'ms'}
        />
        <Tooltip
          content={({ active, payload }: { active?: boolean; payload?: Array<{ payload: { ts: number; latency_ms: number; cache_hit: boolean } }> }) => {
            if (!active || !payload?.length) return null
            const d = payload[0].payload
            return (
              <div className="rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs shadow-lg space-y-0.5">
                <p className="text-stone-400">{new Date(d.ts).toLocaleString()}</p>
                <p className="font-medium text-stone-800">{d.latency_ms.toFixed(1)} ms</p>
                <p className={d.cache_hit ? 'text-green-600' : 'text-violet-600'}>{d.cache_hit ? 'Cache hit' : 'Cache miss'}</p>
              </div>
            )
          }}
        />
        {expLines.map((e, i) => (
          <ReferenceLine
            key={i} x={e.ts} stroke="#D97706" strokeDasharray="4 3"
            label={{ value: e.label, position: 'insideTopLeft', fill: '#92400E', fontSize: 9, offset: 4 }}
          />
        ))}
        <Line dataKey="latency_ms" stroke="#2563EB" strokeWidth={1.5} dot={false} activeDot={{ r: 4, fill: '#2563EB', strokeWidth: 0 }} type="monotone" />
      </LineChart>
    </ResponsiveContainer>
  )
}

// ── Snapshot comparison table ─────────────────────────────────────────────────

interface SnapRow {
  label: string
  requests: number
  errorRatePct: number | null
  hitRatePct: number | null
  fallbackRatePct: number | null
  avgLatencyAll: number | null
  p95LatencyAll: number | null
  avgLatencyHit: number | null
  p95LatencyHit: number | null
  avgLatencyMiss: number | null
  p95LatencyMiss: number | null
  totalCostUsd: number | null
  estSavedUsd: number | null
}

function toSnapRow(label: string, m: MetricsSummary): SnapRow {
  return {
    label,
    requests: m.totals.requests,
    errorRatePct: m.totals.error_rate_pct,
    hitRatePct: m.cache.hit_rate_pct,
    fallbackRatePct: m.totals.fallback_rate_pct,
    avgLatencyAll: m.latency_ms.all.avg,
    p95LatencyAll: m.latency_ms.all.p95,
    avgLatencyHit: m.latency_ms.hit.avg,
    p95LatencyHit: m.latency_ms.hit.p95,
    avgLatencyMiss: m.latency_ms.miss.avg,
    p95LatencyMiss: m.latency_ms.miss.p95,
    totalCostUsd: m.cost.total_usd,
    estSavedUsd: m.cost.est_saved_usd,
  }
}

// true = lower is better, false = higher is better, null = neutral
function lib(key: keyof SnapRow): boolean | null {
  if (key === 'requests' || key === 'label') return null
  if (key === 'hitRatePct' || key === 'estSavedUsd') return false
  return true
}

const CMP_ROWS: { key: keyof SnapRow; label: string; fmt: (v: number | null) => string }[] = [
  { key: 'requests',        label: 'Requests',            fmt: fnum },
  { key: 'errorRatePct',    label: 'Error rate',           fmt: fpct },
  { key: 'hitRatePct',      label: 'Cache hit rate',       fmt: fpct },
  { key: 'fallbackRatePct', label: 'Fallback rate',        fmt: fpct },
  { key: 'avgLatencyAll',   label: 'Avg latency (all)',    fmt: fms  },
  { key: 'p95LatencyAll',   label: 'p95 latency (all)',    fmt: fms  },
  { key: 'avgLatencyHit',   label: 'Avg latency (hit)',    fmt: fms  },
  { key: 'p95LatencyHit',   label: 'p95 latency (hit)',    fmt: fms  },
  { key: 'avgLatencyMiss',  label: 'Avg latency (miss)',   fmt: fms  },
  { key: 'p95LatencyMiss',  label: 'p95 latency (miss)',   fmt: fms  },
  { key: 'totalCostUsd',    label: 'Total cost',           fmt: fusd },
  { key: 'estSavedUsd',     label: 'Est. saved',           fmt: fusd },
]

function DeltaCell({ prev, curr, metricKey, fmt }: {
  prev: number | null; curr: number | null; metricKey: keyof SnapRow; fmt: (v: number | null) => string
}) {
  if (prev == null || curr == null) return <td className="px-2 py-2 text-center"><span className="text-stone-200 text-xs">--</span></td>
  const delta = curr - prev
  const direction = lib(metricKey)
  const improved = direction === null ? null : direction ? delta < 0 : delta > 0
  const cls = improved === null ? 'text-stone-400' : improved ? 'text-green-600' : 'text-red-500'
  return (
    <td className="px-2 py-2 text-center">
      <span className={`text-xs font-medium tabular-nums ${cls}`}>{delta > 0 ? '+' : ''}{fmt(delta)}</span>
    </td>
  )
}

function SnapshotComparisonTable({ snapshots, live }: { snapshots: MetricsSnapshot[]; live: MetricsSummary | null }) {
  const cols: SnapRow[] = [
    ...snapshots.map(s => toSnapRow(s.label, s.metrics)),
    ...(live ? [toSnapRow('Live', live)] : []),
  ]
  if (cols.length === 0) return null
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-stone-100">
            <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-widest text-stone-400 w-40">Metric</th>
            {cols.map((c, i) => (
              <th key={i} colSpan={i > 0 ? 2 : 1}
                className={`px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-widest whitespace-nowrap ${c.label === 'Live' ? 'text-amber-700 bg-amber-50' : 'text-stone-400'}`}
              >{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {CMP_ROWS.map(({ key, label, fmt }) => (
            <tr key={key} className="border-b border-stone-50 hover:bg-stone-50 transition-colors">
              <td className="px-3 py-2 text-xs font-medium text-stone-500">{label}</td>
              {cols.map((c, ci) => (
                <React.Fragment key={ci}>
                  <td className={`px-3 py-2 text-right tabular-nums text-sm ${c.label === 'Live' ? 'bg-amber-50/50 font-medium text-stone-800' : 'text-stone-700'}`}>
                    {c[key] == null ? <NR /> : fmt(c[key] as number)}
                  </td>
                  {ci > 0 && (
                    <DeltaCell
                      prev={cols[ci - 1][key] as number | null}
                      curr={c[key] as number | null}
                      metricKey={key}
                      fmt={fmt}
                    />
                  )}
                </React.Fragment>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Page skeleton ─────────────────────────────────────────────────────────────

function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
      </div>
      <Skeleton className="h-48 rounded-xl" />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Skeleton className="h-56 rounded-xl" />
        <Skeleton className="h-56 rounded-xl" />
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function BaselinePage() {
  const [summary,     setSummary]     = useState<MetricsSummary | null>(null)
  const [snapshots,   setSnapshots]   = useState<MetricsSnapshot[]>([])
  const [experiments, setExperiments] = useState<ExperimentRecord[]>([])
  const [requests,    setRequests]    = useState<RequestRecord[]>([])
  const [loading,     setLoading]     = useState(true)
  const [refreshing,  setRefreshing]  = useState(false)
  const [loadError,   setLoadError]   = useState<string | null>(null)
  const [showDialog,  setShowDialog]  = useState(false)

  const [win, setWin] = useState<WindowState>({ mode: 'all', since: '', until: '' })

  function buildParams(): { since?: string; until?: string } {
    if (win.mode === '24h') {
      const d = new Date(); d.setHours(d.getHours() - 24)
      return { since: d.toISOString() }
    }
    if (win.mode === 'since') {
      return {
        since: win.since ? new Date(win.since).toISOString() : undefined,
        until: win.until ? new Date(win.until).toISOString() : undefined,
      }
    }
    return {}
  }

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    else { setLoading(true); setLoadError(null) }
    try {
      const { since, until } = buildParams()
      const [sum, snaps, exps, reqs] = await Promise.all([
        fetchMetricsSummary(since, until),
        fetchMetricsSnapshots(),
        fetchExperiments(),
        fetchRequests(),
      ])
      setSummary(sum); setSnapshots(snaps); setExperiments(exps); setRequests(reqs)
    } catch (err) {
      const msg = (err as Error).message || 'Failed to load baseline metrics.'
      setLoadError(msg); toast.error(msg)
    } finally { setLoading(false); setRefreshing(false) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [win])

  const isFirst = useRef(true)
  useEffect(() => {
    if (isFirst.current) { isFirst.current = false; load(); return }
    load()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [win.mode, win.since, win.until])

  const { since, until } = buildParams()

  async function handleSaveSnapshot(label: string) {
    const snap = await saveMetricsSnapshot(label, since, until)
    setSnapshots(prev => [...prev, snap])
    toast.success(`Snapshot "${label}" saved.`)
  }

  const isEmpty = !loading && !loadError && summary && summary.totals.requests === 0

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-6xl mx-auto">
      {showDialog && (
        <SaveSnapshotDialog onClose={() => setShowDialog(false)} onSave={handleSaveSnapshot} since={since} until={until} />
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-4 justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Gauge className="w-5 h-5 text-stone-400" />
            <h1 className="text-xl font-semibold text-stone-900">Baseline</h1>
          </div>
          <p className="text-sm text-stone-500 mt-0.5">
            Every real number the gateway has measured. n&nbsp;&lt;&nbsp;30 shows a warning badge.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button variant="outline" size="sm" onClick={() => setShowDialog(true)} disabled={loading || !summary} className="gap-1.5">
            <Save className="w-3.5 h-3.5" />Save snapshot
          </Button>
          <Button variant="outline" size="sm" onClick={() => load(true)} disabled={refreshing} className="gap-1.5">
            {refreshing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            Refresh
          </Button>
        </div>
      </div>

      <WindowSelector state={win} onChange={setWin} />

      {/* Error */}
      {loadError && !loading && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="pt-5 flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-sm font-medium text-red-800">Failed to load baseline metrics</p>
              <p className="text-xs text-red-600 mt-0.5">{loadError}</p>
            </div>
            <Button variant="outline" size="sm" onClick={() => load()} className="shrink-0">Retry</Button>
          </CardContent>
        </Card>
      )}

      {/* Skeleton */}
      {loading && <PageSkeleton />}

      {/* Empty */}
      {isEmpty && (
        <div className="flex flex-col items-center justify-center py-24 gap-3 text-stone-400">
          <Gauge className="w-12 h-12 opacity-20" />
          <p className="text-sm">No requests recorded in this time window yet.</p>
          <p className="text-xs text-stone-300">Run some queries and then refresh.</p>
        </div>
      )}

      {!loading && !loadError && summary && !isEmpty && (
        <>
          {/* 1. Top metric cards */}
          <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
            <MetricCard title="Total requests" value={summary.totals.requests.toLocaleString()}
              sub={<span className="text-xs text-stone-400">window: {summary.window.request_count.toLocaleString()}</span>} />
            <MetricCard title="Error rate" value={fpct(summary.totals.error_rate_pct)}
              sub={<span className="text-xs text-stone-400">{summary.totals.errors} error{summary.totals.errors !== 1 ? 's' : ''}</span>} />
            <MetricCard title="Cache hit rate" value={fpct(summary.cache.hit_rate_pct)} accent
              sub={<span className="text-xs text-stone-400">{summary.cache.hits.toLocaleString()} hits</span>} />
            <MetricCard title="Fallback rate" value={fpct(summary.totals.fallback_rate_pct)}
              sub={<span className="text-xs text-stone-400">{summary.totals.fallback_count} fallback{summary.totals.fallback_count !== 1 ? 's' : ''}</span>} />
            <MetricCard title="Total cost" value={fusd(summary.cost.total_usd)} accent />
          </div>

          {/* Legacy warning */}
          {summary.legacy.hit_rows_with_zero_latency > 0 && (
            <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
              <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
              <p className="text-sm text-amber-800">
                <span className="font-semibold">{summary.legacy.hit_rows_with_zero_latency.toLocaleString()}</span>{' '}
                cache-hit row{summary.legacy.hit_rows_with_zero_latency !== 1 ? 's were' : ' was'} logged before accurate
                accounting and show 0 latency / cost. They are excluded from hit latency averages.
              </p>
            </div>
          )}

          {/* 2. Latency */}
          <Card>
            <CardHeader><CardTitle>Latency (ms)</CardTitle></CardHeader>
            <CardContent className="space-y-6">
              <LatencyTable data={summary.latency_ms} />
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-stone-400 mb-3">p50 &amp; p95 — hit vs miss</p>
                <LatencyBarChart data={summary.latency_ms} />
              </div>
            </CardContent>
          </Card>

          {/* 3. Embedding overhead */}
          <Card>
            <CardHeader><CardTitle>Embedding overhead</CardTitle></CardHeader>
            <CardContent>
              {summary.embed.rows_with_data === 0 ? (
                <div className="flex flex-col items-center justify-center py-10 gap-2 text-stone-400">
                  <Info className="w-8 h-8 opacity-30" />
                  <p className="text-sm">No embedding data recorded yet.</p>
                  <p className="text-xs text-stone-300">Embedding metrics appear after the first cache-miss that triggers embedding.</p>
                </div>
              ) : (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {[
                    { label: 'Avg embed latency',    value: fms(summary.embed.avg_latency_ms) },
                    { label: 'p95 embed latency',    value: fms(summary.embed.p95_latency_ms) },
                    { label: 'Avg embed cost',        value: fusd(summary.embed.avg_cost_usd) },
                    { label: 'Share of miss latency', value: fpct(summary.embed.share_of_miss_latency_pct) },
                  ].map(({ label, value }) => (
                    <div key={label} className="flex flex-col rounded-lg border border-stone-100 bg-stone-50 px-4 py-3">
                      <span className="text-[10px] font-semibold uppercase tracking-widest text-stone-400 mb-1">{label}</span>
                      <span className="text-lg font-semibold text-stone-800 tabular-nums leading-tight">
                        {value === 'not recorded' ? <NR /> : value}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* 4. Tables row — cache by scope + providers */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader><CardTitle>Cache by scope</CardTitle></CardHeader>
              <CardContent className="pt-0">
                {summary.cache.by_scope.length === 0 ? (
                  <p className="text-xs text-stone-400 py-4 text-center">No scope data.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                      <thead><tr className="border-b border-stone-100"><Th>Scope</Th><Th right>Requests</Th><Th right>Hits</Th><Th right>Hit rate</Th></tr></thead>
                      <tbody>
                        {summary.cache.by_scope.map(row => (
                          <tr key={row.scope} className="border-b border-stone-50 hover:bg-stone-50 transition-colors">
                            <Td><Badge variant="default" className="text-[10px]">{row.scope}</Badge></Td>
                            <Td right mono>{row.requests.toLocaleString()}</Td>
                            <Td right mono>{row.hits.toLocaleString()}</Td>
                            <Td right mono>{row.hit_rate_pct == null ? <NR /> : fpct(row.hit_rate_pct)}</Td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle>By provider</CardTitle></CardHeader>
              <CardContent className="pt-0">
                {summary.by_provider.length === 0 ? (
                  <p className="text-xs text-stone-400 py-4 text-center">No provider data.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                      <thead><tr className="border-b border-stone-100"><Th>Provider</Th><Th right>Reqs</Th><Th right>Avg ms</Th><Th right>p95 ms</Th><Th right>Avg cost</Th><Th right>Fallbacks</Th></tr></thead>
                      <tbody>
                        {summary.by_provider.map(p => (
                          <tr key={p.provider} className="border-b border-stone-50 hover:bg-stone-50 transition-colors">
                            <Td><span className="capitalize font-medium text-stone-700">{p.provider}</span></Td>
                            <Td right mono>{p.requests.toLocaleString()}</Td>
                            <Td right mono>{p.avg_latency_ms == null ? <NR /> : fms(p.avg_latency_ms)}</Td>
                            <Td right mono>{p.p95_latency_ms == null ? <NR /> : fms(p.p95_latency_ms)}</Td>
                            <Td right mono>{p.avg_cost_usd == null ? <NR /> : fusd(p.avg_cost_usd)}</Td>
                            <Td right mono>{p.fallbacks}</Td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Error types + breaker transitions */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card>
              <CardHeader><CardTitle>Error types</CardTitle></CardHeader>
              <CardContent className="pt-0">
                {summary.error_types.length === 0 ? (
                  <p className="text-xs text-stone-400 py-4 text-center">No errors recorded.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                      <thead><tr className="border-b border-stone-100"><Th>Error type</Th><Th right>Count</Th></tr></thead>
                      <tbody>
                        {summary.error_types.map(e => (
                          <tr key={e.error_type} className="border-b border-stone-50 hover:bg-stone-50 transition-colors">
                            <Td><code className="text-xs bg-red-50 text-red-700 rounded px-1.5 py-0.5 border border-red-100">{e.error_type}</code></Td>
                            <Td right mono>{e.count.toLocaleString()}</Td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle>Breaker transitions</CardTitle></CardHeader>
              <CardContent className="pt-0">
                {summary.breaker.transitions.length === 0 ? (
                  <p className="text-xs text-stone-400 py-4 text-center">No breaker transitions in this window.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                      <thead><tr className="border-b border-stone-100"><Th>Provider</Th><Th>New state</Th><Th right>Count</Th></tr></thead>
                      <tbody>
                        {summary.breaker.transitions.map((t, i) => (
                          <tr key={i} className="border-b border-stone-50 hover:bg-stone-50 transition-colors">
                            <Td><span className="capitalize font-medium text-stone-700">{t.provider}</span></Td>
                            <Td>
                              <Badge className={
                                t.new_state === 'closed' ? 'border-green-200 bg-green-50 text-green-800'
                                : t.new_state === 'open' ? 'border-red-200 bg-red-50 text-red-700'
                                : 'border-amber-200 bg-amber-50 text-amber-800'
                              }>{t.new_state}</Badge>
                            </Td>
                            <Td right mono>{t.count.toLocaleString()}</Td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* 5. Cost breakdown */}
          <Card>
            <CardHeader><CardTitle>Cost breakdown</CardTitle></CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {([
                  { label: 'Total cost',   value: fusd(summary.cost.total_usd),   estimate: false },
                  { label: 'Avg miss cost', value: fusd(summary.cost.avg_miss_usd), estimate: false },
                  { label: 'Avg hit cost',  value: fusd(summary.cost.avg_hit_usd),  estimate: false },
                  { label: 'Est. saved',   value: fusd(summary.cost.est_saved_usd), estimate: true  },
                ] as const).map(({ label, value, estimate }) => (
                  <div key={label} className="flex flex-col rounded-lg border border-stone-100 bg-stone-50 px-4 py-3">
                    <div className="flex items-center gap-1.5 mb-1">
                      <span className="text-[10px] font-semibold uppercase tracking-widest text-stone-400">{label}</span>
                      {estimate && (
                        <span title="Computed as hits x average miss cost. Not a directly measured value."
                          className="inline-flex items-center gap-0.5 rounded border border-stone-200 bg-white px-1 py-0 text-[9px] font-semibold text-stone-400 leading-4 cursor-help">
                          <Info className="w-2 h-2" />ESTIMATE
                        </span>
                      )}
                    </div>
                    <span className="text-lg font-semibold text-stone-800 tabular-nums leading-tight">
                      {value === 'not recorded' ? <NR /> : value}
                    </span>
                  </div>
                ))}
              </div>
              <p className="text-xs text-stone-400 mt-4 flex items-center gap-1">
                <Info className="w-3 h-3 shrink-0" />
                "Est. saved" = hits x avg miss cost. It is an estimate, not a direct measurement.
              </p>
            </CardContent>
          </Card>

          {/* 6. Token estimates (only if data exists) */}
          {(summary.tokens.avg_estimated != null || summary.tokens.p95_estimated != null) && (
            <Card>
              <CardHeader><CardTitle>Token usage (estimated)</CardTitle></CardHeader>
              <CardContent>
                <div className="flex gap-6">
                  <div>
                    <p className="text-[10px] font-semibold uppercase tracking-widest text-stone-400 mb-1">Avg tokens</p>
                    <p className="text-xl font-semibold text-stone-800 tabular-nums">{fnum(summary.tokens.avg_estimated)}</p>
                  </div>
                  <div>
                    <p className="text-[10px] font-semibold uppercase tracking-widest text-stone-400 mb-1">p95 tokens</p>
                    <p className="text-xl font-semibold text-stone-800 tabular-nums">{fnum(summary.tokens.p95_estimated)}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* 7. Latency over time */}
          <Card>
            <CardHeader><CardTitle>Latency over time</CardTitle></CardHeader>
            <CardContent>
              <LatencyTimelineChart requests={requests} experiments={experiments} />
              {experiments.length > 0 && (
                <p className="text-xs text-stone-400 mt-2">Vertical amber dashed lines mark experiment timestamps.</p>
              )}
            </CardContent>
          </Card>

          {/* 8. Snapshots */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Snapshots comparison</CardTitle>
                <Button variant="outline" size="sm" onClick={() => setShowDialog(true)} className="gap-1.5">
                  <Save className="w-3.5 h-3.5" />Save snapshot
                </Button>
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              {snapshots.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-10 gap-2 text-stone-400">
                  <Save className="w-8 h-8 opacity-20" />
                  <p className="text-sm">No snapshots saved yet.</p>
                  <p className="text-xs text-stone-300">Save a snapshot to track how metrics change over time.</p>
                </div>
              ) : (
                <>
                  <p className="text-xs text-stone-400 mb-4">
                    Deltas between adjacent columns are{' '}
                    <span className="text-green-600 font-medium">green</span> when improved and{' '}
                    <span className="text-red-500 font-medium">red</span> when regressed.
                  </p>
                  <SnapshotComparisonTable snapshots={snapshots} live={summary} />
                </>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
