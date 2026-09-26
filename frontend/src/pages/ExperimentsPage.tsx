import { useEffect, useState, useCallback, useMemo } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { FlaskConical, RefreshCw, Loader2, ShieldCheck } from 'lucide-react'
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

// ── Chart wrapper ─────────────────────────────────────────────────────────────
function ChartCard({
  title,
  children,
  loading,
}: {
  title: string
  children: React.ReactNode
  loading?: boolean
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? <Skeleton className="h-48 w-full" /> : children}
      </CardContent>
    </Card>
  )
}

// ── Breaker state badge ────────────────────────────────────────────────────────
const BREAKER_STYLES = {
  closed: {
    dot: 'bg-green-500',
    badge: 'border-green-200 bg-green-50 text-green-800',
    label: 'Closed',
  },
  open: {
    dot: 'bg-red-500',
    badge: 'border-red-200 bg-red-50 text-red-700',
    label: 'Open',
  },
  half_open: {
    dot: 'bg-amber-500',
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

// ── Main page ─────────────────────────────────────────────────────────────────
export function ExperimentsPage() {
  const [experiments, setExperiments] = useState<ExperimentRecord[]>([])
  const [breakers, setBreakers] = useState<BreakerStatusMap>({})
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [selectedMetric, setSelectedMetric] = useState<string>('')

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

  // ── Derive all unique numeric metric keys across all runs ──────────────────
  const allMetricKeys = useMemo(() => {
    const keys = new Set<string>()
    for (const exp of experiments) {
      for (const [k, v] of Object.entries(exp.metrics)) {
        if (typeof v === 'number') keys.add(k)
      }
    }
    return [...keys]
  }, [experiments])

  // Default to first key when data loads
  useEffect(() => {
    if (!selectedMetric && allMetricKeys.length > 0) {
      setSelectedMetric(allMetricKeys[0])
    }
  }, [allMetricKeys, selectedMetric])

  // ── Bar chart data ─────────────────────────────────────────────────────────
  const barData = useMemo(
    () =>
      experiments
        .filter((e) => typeof e.metrics[selectedMetric] === 'number')
        .map((e) => ({
          name: e.name,
          value: e.metrics[selectedMetric],
        })),
    [experiments, selectedMetric]
  )

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

      {/* Metric bar chart */}
      <ChartCard
        title={selectedMetric ? `Metric Comparison — ${selectedMetric}` : 'Metric Comparison'}
        loading={loading}
      >
        {experiments.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-sm text-stone-400">
            No experiment data available
          </div>
        ) : (
          <>
            {/* Metric selector */}
            <div className="flex items-center gap-2 mb-4">
              <label
                htmlFor="metric-select"
                className="text-xs text-stone-500 whitespace-nowrap"
              >
                Metric
              </label>
              <select
                id="metric-select"
                value={selectedMetric}
                onChange={(e) => setSelectedMetric(e.target.value)}
                className="text-xs border border-stone-200 rounded-md px-2 py-1.5 bg-white text-stone-700 focus:outline-none focus:ring-2 focus:ring-amber-500 focus:border-amber-500 transition-colors"
              >
                {allMetricKeys.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </div>

            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={barData} barSize={32} margin={{ bottom: 4 }}>
                <CartesianGrid vertical={false} stroke="#F5F5F4" />
                <XAxis
                  dataKey="name"
                  tick={{ fill: '#A8A29E', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  interval={0}
                  angle={barData.length > 5 ? -25 : 0}
                  textAnchor={barData.length > 5 ? 'end' : 'middle'}
                  height={barData.length > 5 ? 48 : 28}
                />
                <YAxis
                  tick={{ fill: '#A8A29E', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={48}
                  tickFormatter={(v: number) =>
                    v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
                  }
                />
                <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(0,0,0,0.03)' }} />
                <Bar dataKey="value" name={selectedMetric} radius={[4, 4, 0, 0]} fill="#D97706" />
              </BarChart>
            </ResponsiveContainer>
          </>
        )}
      </ChartCard>

      {/* Experiments table */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FlaskConical className="w-4 h-4 text-stone-400" />
            <CardTitle>Experiment Runs</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-10 w-full rounded-lg" />
              ))}
            </div>
          ) : experiments.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 gap-2 text-stone-400">
              <FlaskConical className="w-8 h-8 opacity-30" />
              <p className="text-sm">No experiment runs recorded yet.</p>
            </div>
          ) : (
            <div className="overflow-x-auto -mx-5">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-stone-100">
                    <th className="px-5 py-2.5 text-left text-xs font-medium uppercase tracking-widest text-stone-400 whitespace-nowrap">
                      Name
                    </th>
                    <th className="px-5 py-2.5 text-left text-xs font-medium uppercase tracking-widest text-stone-400 whitespace-nowrap">
                      Category
                    </th>
                    <th className="px-5 py-2.5 text-left text-xs font-medium uppercase tracking-widest text-stone-400">
                      Description
                    </th>
                    <th className="px-5 py-2.5 text-left text-xs font-medium uppercase tracking-widest text-stone-400 whitespace-nowrap">
                      Timestamp
                    </th>
                    {allMetricKeys.map((k) => (
                      <th
                        key={k}
                        className="px-5 py-2.5 text-right text-xs font-medium uppercase tracking-widest text-stone-400 whitespace-nowrap"
                      >
                        {k}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {experiments.map((exp, i) => (
                    <tr
                      key={i}
                      className="border-b border-stone-50 hover:bg-stone-50 transition-colors duration-100"
                    >
                      <td className="px-5 py-3 font-medium text-stone-800 whitespace-nowrap">
                        {exp.name}
                      </td>
                      <td className="px-5 py-3 whitespace-nowrap">
                        <span className="inline-flex items-center rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800">
                          {exp.category}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-stone-500 max-w-[260px] truncate">
                        {exp.description}
                      </td>
                      <td className="px-5 py-3 text-stone-400 whitespace-nowrap">
                        {new Date(exp.timestamp).toLocaleString()}
                      </td>
                      {allMetricKeys.map((k) => (
                        <td
                          key={k}
                          className="px-5 py-3 text-right text-stone-700 whitespace-nowrap tabular-nums"
                        >
                          {typeof exp.metrics[k] === 'number'
                            ? exp.metrics[k].toLocaleString(undefined, {
                                maximumFractionDigits: 4,
                              })
                            : '—'}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
