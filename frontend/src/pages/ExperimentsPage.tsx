import { useEffect, useState, useCallback, useMemo } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts'
import { FlaskConical, RefreshCw, Loader2, ShieldCheck, TrendingDown, TrendingUp, Minus } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  fetchExperiments,
  fetchBreakerStatus,
  type ExperimentRecord,
  type BreakerStatusMap,
} from '@/lib/api'

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Format ISO timestamp to e.g. "Sep 26, 2026" */
function formatDate(ts: string): string {
  return new Date(ts).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

/**
 * Find shared numeric metric keys between two experiments where values differ
 * by more than 5% relative.
 */
function findSharedMetricKeys(a: ExperimentRecord, b: ExperimentRecord): string[] {
  return Object.keys(a.metrics).filter((k) => {
    const av = a.metrics[k]
    const bv = b.metrics[k]
    if (typeof av !== 'number' || typeof bv !== 'number') return false
    if (av === bv) return false
    const max = Math.max(Math.abs(av), Math.abs(bv))
    if (max === 0) return false
    return Math.abs(av - bv) / max > 0.05
  })
}

/**
 * Build a plain-language delta sentence for a single metric key.
 * Returns the text and a direction indicator.
 */
function buildDeltaLine(
  beforeVal: number,
  afterVal: number,
  metricKey: string
): { text: string; direction: 'down' | 'up' | 'same' } {
  const label = metricKey.replace(/_/g, ' ')

  if (beforeVal === 0) {
    return { text: `${label} went from 0 to ${afterVal.toLocaleString()}`, direction: 'up' }
  }

  const pct = ((afterVal - beforeVal) / Math.abs(beforeVal)) * 100
  const absPct = Math.abs(pct).toFixed(0)

  if (pct < -1) {
    return { text: `${absPct}% fewer ${label} after the change`, direction: 'down' }
  } else if (pct > 1) {
    return { text: `${absPct}% more ${label} after the change`, direction: 'up' }
  } else {
    return { text: `${label} stayed roughly the same`, direction: 'same' }
  }
}

// ── Category badge palette ────────────────────────────────────────────────────

const CATEGORY_COLORS: Record<string, string> = {
  routing:     'border-amber-200  bg-amber-50  text-amber-800',
  reliability: 'border-blue-200   bg-blue-50   text-blue-800',
  adapter:     'border-violet-200 bg-violet-50 text-violet-800',
  cache:       'border-green-200  bg-green-50  text-green-800',
  cost:        'border-orange-200 bg-orange-50 text-orange-800',
}

function categoryBadgeClass(category: string): string {
  return (
    CATEGORY_COLORS[category.toLowerCase()] ??
    'border-stone-200 bg-stone-100 text-stone-700'
  )
}

// ── Custom tooltip ────────────────────────────────────────────────────────────
function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs shadow-lg">
      {label && <p className="text-stone-400 mb-1.5 truncate max-w-[160px]">{label}</p>}
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }} className="flex gap-2 items-center">
          <span className="text-stone-500">{p.name}:</span>
          <span className="font-medium text-stone-800">
            {typeof p.value === 'number'
              ? p.value.toLocaleString(undefined, { maximumFractionDigits: 4 })
              : p.value}
          </span>
        </p>
      ))}
    </div>
  )
}

// ── Breaker state badge ────────────────────────────────────────────────────────
const BREAKER_STYLES = {
  closed: {
    dot:   'bg-green-500',
    badge: 'border-green-200 bg-green-50 text-green-800',
    label: 'Closed',
  },
  open: {
    dot:   'bg-red-500',
    badge: 'border-red-200 bg-red-50 text-red-700',
    label: 'Open',
  },
  half_open: {
    dot:   'bg-amber-500',
    badge: 'border-amber-200 bg-amber-50 text-amber-800',
    label: 'Half-open',
  },
} as const

function BreakerBadge({ state }: { state: 'closed' | 'open' | 'half_open' }) {
  const s = BREAKER_STYLES[state] ?? BREAKER_STYLES.open
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${s.badge}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  )
}

// ── Pair comparison card ──────────────────────────────────────────────────────
interface PairCardProps {
  before: ExperimentRecord
  after: ExperimentRecord
  sharedKeys: string[]
}

function PairCard({ before, after, sharedKeys }: PairCardProps) {
  const primaryKey = sharedKeys[0]
  const chartData = [
    { label: 'Before', value: before.metrics[primaryKey] },
    { label: 'After',  value: after.metrics[primaryKey]  },
  ]

  return (
    <Card className="overflow-hidden">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="space-y-1 min-w-0">
            <CardTitle>
              {before.name.replace(/_(before|after)$/i, '').replace(/_/g, ' ')}
            </CardTitle>
            <p className="text-sm text-stone-700 leading-relaxed whitespace-normal">
              {before.description}
            </p>
          </div>
          <span
            className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium shrink-0 ${categoryBadgeClass(before.category)}`}
          >
            {before.category}
          </span>
        </div>
      </CardHeader>

      <CardContent>
        <div className="flex flex-col lg:flex-row gap-6">
          {/* Two-bar comparison chart */}
          <div className="lg:w-64 shrink-0">
            <p className="text-xs text-stone-400 mb-2 uppercase tracking-wider">
              {primaryKey.replace(/_/g, ' ')}
            </p>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={chartData} barSize={40} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
                <CartesianGrid vertical={false} stroke="#F5F5F4" />
                <XAxis
                  dataKey="label"
                  tick={{ fill: '#A8A29E', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fill: '#A8A29E', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={42}
                  tickFormatter={(v: number) =>
                    v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
                  }
                />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(0,0,0,0.03)' }} />
                <Bar dataKey="value" name={primaryKey} radius={[4, 4, 0, 0]}>
                  {/* before = stone, after = amber to match dashboard palette */}
                  <Cell fill="#A8A29E" />
                  <Cell fill="#D97706" />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Delta lines for every shared key */}
          <div className="flex flex-col justify-center gap-3 min-w-0">
            {sharedKeys.map((key) => {
              const bv = before.metrics[key]
              const av = after.metrics[key]
              const { text, direction } = buildDeltaLine(bv, av, key)
              return (
                <div key={key} className="flex items-start gap-2">
                  <span className="mt-0.5 shrink-0">
                    {direction === 'down' ? (
                      <TrendingDown className="w-4 h-4 text-green-600" />
                    ) : direction === 'up' ? (
                      <TrendingUp className="w-4 h-4 text-amber-600" />
                    ) : (
                      <Minus className="w-4 h-4 text-stone-400" />
                    )}
                  </span>
                  <div className="min-w-0">
                    <p className="text-sm text-stone-700">{text}</p>
                    <p className="text-xs text-stone-400 mt-0.5 tabular-nums">
                      {bv.toLocaleString()} → {av.toLocaleString()}
                    </p>
                  </div>
                </div>
              )
            })}

            {/* Timestamps */}
            <div className="flex gap-4 text-xs text-stone-400 mt-1 flex-wrap">
              <span>Before: {formatDate(before.timestamp)}</span>
              <span>After: {formatDate(after.timestamp)}</span>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// ── Standalone experiment card ────────────────────────────────────────────────
function StandaloneCard({ exp }: { exp: ExperimentRecord }) {
  const numericEntries = Object.entries(exp.metrics).filter(
    ([, v]) => typeof v === 'number'
  ) as [string, number][]

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="space-y-0.5 min-w-0">
            <CardTitle>{exp.name.replace(/_/g, ' ')}</CardTitle>
            <p className="text-xs text-stone-400">{formatDate(exp.timestamp)}</p>
          </div>
          <span
            className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium shrink-0 ${categoryBadgeClass(exp.category)}`}
          >
            {exp.category}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Full description – no truncation */}
        <p className="text-sm text-stone-600 leading-relaxed whitespace-normal">
          {exp.description}
        </p>

        {/* Metric stat chips */}
        {numericEntries.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {numericEntries.map(([key, val]) => (
              <div
                key={key}
                className="flex flex-col items-center rounded-lg border border-stone-100 bg-stone-50 px-3 py-2 min-w-[72px]"
              >
                <span className="text-lg font-semibold text-stone-800 tabular-nums leading-tight">
                  {val.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                </span>
                <span className="text-[10px] text-stone-400 uppercase tracking-wide mt-0.5 text-center">
                  {key.replace(/_/g, ' ')}
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── Skeleton loading for experiment sections ──────────────────────────────────
function ExperimentsSkeleton() {
  return (
    <div className="space-y-8">
      {[1, 2].map((g) => (
        <div key={g} className="space-y-3">
          <Skeleton className="h-5 w-32 rounded" />
          <div className="space-y-3">
            {[1, 2].map((i) => (
              <Skeleton key={i} className="h-40 w-full rounded-xl" />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Category section ──────────────────────────────────────────────────────────
interface CategorySectionProps {
  category: string
  experiments: ExperimentRecord[]
}

function CategorySection({ category, experiments }: CategorySectionProps) {
  const paired = new Set<number>()
  const pairs: Array<{
    before: ExperimentRecord
    after: ExperimentRecord
    sharedKeys: string[]
  }> = []

  // Pass 1 – name-based pairing (_before / _after suffixes)
  for (let i = 0; i < experiments.length; i++) {
    if (paired.has(i)) continue
    const isBefore = /_before$/i.test(experiments[i].name)
    const isAfter  = /_after$/i.test(experiments[i].name)
    if (!isBefore && !isAfter) continue

    const stem        = experiments[i].name.replace(/_(before|after)$/i, '')
    const wantSuffix  = isBefore ? '_after' : '_before'
    const partnerIdx  = experiments.findIndex(
      (e, j) =>
        j !== i &&
        !paired.has(j) &&
        e.name.toLowerCase() === `${stem}${wantSuffix}`.toLowerCase()
    )
    if (partnerIdx === -1) continue

    const before     = isBefore ? experiments[i] : experiments[partnerIdx]
    const after      = isBefore ? experiments[partnerIdx] : experiments[i]
    const sharedKeys = findSharedMetricKeys(before, after)

    paired.add(i)
    paired.add(partnerIdx)
    pairs.push({
      before,
      after,
      sharedKeys: sharedKeys.length
        ? sharedKeys
        : Object.keys(before.metrics).filter((k) => typeof before.metrics[k] === 'number'),
    })
  }

  // Pass 2 – metric-based pairing for anything still unpaired
  const remaining = experiments
    .map((exp, origIdx) => ({ exp, origIdx }))
    .filter(({ origIdx }) => !paired.has(origIdx))

  for (let i = 0; i < remaining.length; i++) {
    if (paired.has(remaining[i].origIdx)) continue
    for (let j = i + 1; j < remaining.length; j++) {
      if (paired.has(remaining[j].origIdx)) continue
      const sharedKeys = findSharedMetricKeys(remaining[i].exp, remaining[j].exp)
      if (sharedKeys.length === 0) continue
      // Guess "before" by timestamp order
      const tA   = new Date(remaining[i].exp.timestamp).getTime()
      const tB   = new Date(remaining[j].exp.timestamp).getTime()
      const before = tA <= tB ? remaining[i].exp : remaining[j].exp
      const after  = tA <= tB ? remaining[j].exp : remaining[i].exp
      paired.add(remaining[i].origIdx)
      paired.add(remaining[j].origIdx)
      pairs.push({ before, after, sharedKeys })
      break
    }
  }

  const soloExps = experiments.filter((_, i) => !paired.has(i))

  return (
    <section className="space-y-3">
      {/* Category heading with divider */}
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-widest text-stone-500">
          {category}
        </h2>
        <div className="flex-1 h-px bg-stone-100" />
        <span className="text-xs text-stone-400">
          {experiments.length} experiment{experiments.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Paired cards (full-width) */}
      {pairs.map(({ before, after, sharedKeys }, idx) => (
        <PairCard key={`pair-${idx}`} before={before} after={after} sharedKeys={sharedKeys} />
      ))}

      {/* Standalone cards (two-column on md+) */}
      {soloExps.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {soloExps.map((exp, idx) => (
            <StandaloneCard key={`solo-${idx}`} exp={exp} />
          ))}
        </div>
      )}
    </section>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export function ExperimentsPage() {
  const [experiments, setExperiments] = useState<ExperimentRecord[]>([])
  const [breakers, setBreakers]       = useState<BreakerStatusMap>({})
  const [loading, setLoading]         = useState(true)
  const [refreshing, setRefreshing]   = useState(false)

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    else setLoading(true)
    try {
      const [exps, bkrs] = await Promise.all([fetchExperiments(), fetchBreakerStatus()])
      setExperiments(exps)
      setBreakers(bkrs)
    } catch (err) {
      toast.error((err as Error).message || 'Failed to load experiments.')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Group experiments by category, preserving insertion order
  const byCategory = useMemo(() => {
    const map = new Map<string, ExperimentRecord[]>()
    for (const exp of experiments) {
      const cat = exp.category || 'uncategorised'
      if (!map.has(cat)) map.set(cat, [])
      map.get(cat)!.push(exp)
    }
    return map
  }, [experiments])

  const breakerEntries = Object.entries(breakers)

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-stone-900">Experiments</h1>
          <p className="text-sm text-stone-500 mt-0.5">Run history and circuit breaker status</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => load(true)}
          disabled={refreshing}
          className="gap-2"
        >
          {refreshing ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <RefreshCw className="w-3.5 h-3.5" />
          )}
          Refresh
        </Button>
      </div>

      {/* Circuit breaker status row */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-stone-400" />
            <CardTitle>Circuit Breakers</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex gap-3 flex-wrap">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-8 w-32 rounded-lg" />
              ))}
            </div>
          ) : breakerEntries.length === 0 ? (
            <p className="text-sm text-stone-400">No circuit breaker data available.</p>
          ) : (
            <div className="flex flex-wrap gap-3">
              {breakerEntries.map(([provider, info]) => (
                <div
                  key={provider}
                  className="flex items-center gap-2.5 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2"
                >
                  <span className="text-sm font-medium text-stone-700 capitalize">{provider}</span>
                  <BreakerBadge state={info.state} />
                  {info.consecutive_failures > 0 && (
                    <span className="text-xs text-stone-400">
                      {info.consecutive_failures} failure
                      {info.consecutive_failures !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Experiment sections grouped by category */}
      {loading ? (
        <ExperimentsSkeleton />
      ) : experiments.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3 text-stone-400">
          <FlaskConical className="w-10 h-10 opacity-25" />
          <p className="text-sm">No experiment runs recorded yet.</p>
        </div>
      ) : (
        <div className="space-y-10">
          {[...byCategory.entries()].map(([category, exps]) => (
            <CategorySection key={category} category={category} experiments={exps} />
          ))}
        </div>
      )}
    </div>
  )
}
