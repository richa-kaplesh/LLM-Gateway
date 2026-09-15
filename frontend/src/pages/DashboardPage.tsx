import { useEffect, useState, useCallback } from 'react'
import {
  BarChart,
  Bar,
  ComposedChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  ZAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'

import { RefreshCw, TrendingUp, DollarSign, Layers, Zap, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardValue } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { fetchGlobalStats, fetchRequests, type GlobalStats, type RequestRecord } from '@/lib/api'

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
      {label && <p className="text-stone-400 mb-1.5">{label}</p>}
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }} className="flex gap-2 items-center">
          <span className="text-stone-500">{p.name}:</span>
          <span className="font-medium text-stone-800">{p.value}</span>
        </p>
      ))}
    </div>
  )
}

// ── Stat card ─────────────────────────────────────────────────────────────────
interface StatCardProps {
  title: string
  value: string
  icon: React.ReactNode
  loading?: boolean
  accent?: boolean
}
function StatCard({ title, value, icon, loading, accent }: StatCardProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>{title}</CardTitle>
          <div className={accent ? 'text-amber-600' : 'text-stone-300'}>{icon}</div>
        </div>
        {loading ? (
          <Skeleton className="h-8 w-28 mt-1" />
        ) : (
          <CardValue className={accent ? 'text-amber-700' : undefined}>{value}</CardValue>
        )}
      </CardHeader>
    </Card>
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

// ── Main page ─────────────────────────────────────────────────────────────────
export function DashboardPage() {
  const [stats, setStats] = useState<GlobalStats | null>(null)
  const [requests, setRequests] = useState<RequestRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    else setLoading(true)
    try {
      const [data, reqs] = await Promise.all([fetchGlobalStats(), fetchRequests()])
      setStats(data)
      setRequests(reqs)
    } catch (err) {
      toast.error((err as Error).message || 'Failed to load stats.')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // ── Derived chart data ──────────────────────────────────────────────────────
  const modelData = stats
    ? [
        { name: 'Groq', requests: stats.groq_requests },
        { name: 'Gemini', requests: stats.gemini_requests },
      ]
    : []

  // ── Chart data (Cost per Request) ───────────────────────────────────────────
  const allSorted = [...requests]
    .map((r, i) => ({ ...r, idx: i }))
    .sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())

  const scatterAllHits = allSorted
    .filter((r) => r.cache_hit)
    .map((r) => ({
      x: r.idx + 1,
      y: parseFloat((r.cost_usd * 1_000_000).toFixed(6)),
      label: new Date(r.timestamp).toLocaleTimeString(),
      model: r.model_used,
    }))

  const scatterAllMisses = allSorted
    .filter((r) => !r.cache_hit)
    .map((r) => ({
      x: r.idx + 1,
      y: parseFloat((r.cost_usd * 1_000_000).toFixed(6)),
      label: new Date(r.timestamp).toLocaleTimeString(),
      model: r.model_used,
    }))

  const hitRate = stats ? Math.round(stats.cache_hit_rate * 100) : 0
  const donutData = [
    { name: 'Cache Hit', value: hitRate },
    { name: 'Miss', value: 100 - hitRate },
  ]
  // Light-adapted: green hit, stone miss
  const DONUT_COLORS = ['#16A34A', '#E7E5E4']

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-stone-900">Analytics Dashboard</h1>
          <p className="text-sm text-stone-500 mt-0.5">Gateway-wide statistics</p>
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

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          title="Total Requests"
          value={stats ? stats.total_requests.toLocaleString() : '—'}
          icon={<Layers className="w-4 h-4" />}
          loading={loading}
        />
        <StatCard
          title="Total Cost"
          value={stats ? `$${stats.total_cost_usd.toFixed(6)}` : '—'}
          icon={<DollarSign className="w-4 h-4" />}
          loading={loading}
        />
        <StatCard
          title="Cost Saved"
          value={stats ? `$${stats.total_cost_saved_usd.toFixed(6)}` : '—'}
          icon={<TrendingUp className="w-4 h-4" />}
          loading={loading}
          accent
        />
        <StatCard
          title="Cache Hit Rate"
          value={stats ? `${hitRate}%` : '—'}
          icon={<Zap className="w-4 h-4" />}
          loading={loading}
          accent
        />
      </div>

      {/* Charts row 1 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {/* Bar chart */}
        <ChartCard title="Requests by Model" loading={loading}>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={modelData} barSize={36}>
              <CartesianGrid vertical={false} stroke="#F5F5F4" />
              <XAxis
                dataKey="name"
                tick={{ fill: '#A8A29E', fontSize: 12 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#A8A29E', fontSize: 12 }}
                axisLine={false}
                tickLine={false}
                width={36}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(0,0,0,0.03)' }} />
              <Bar dataKey="requests" radius={[4, 4, 0, 0]}>
                {/* Groq: amber, Gemini: blue */}
                <Cell fill="#D97706" />
                <Cell fill="#2563EB" />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {/* Donut chart */}
        <ChartCard title="Cache Hit Rate" loading={loading}>
          <div className="flex items-center justify-center gap-8 h-[200px]">
            <ResponsiveContainer width={160} height={160}>
              <PieChart>
                <Pie
                  data={donutData}
                  cx="50%"
                  cy="50%"
                  innerRadius={52}
                  outerRadius={72}
                  dataKey="value"
                  startAngle={90}
                  endAngle={-270}
                  strokeWidth={0}
                >
                  {donutData.map((_, i) => (
                    <Cell key={i} fill={DONUT_COLORS[i]} />
                  ))}
                </Pie>
                <text
                  x="50%"
                  y="50%"
                  textAnchor="middle"
                  dominantBaseline="middle"
                  style={{ fontSize: 20, fontWeight: 600, fill: '#1C1917' }}
                >
                  {hitRate}%
                </text>
              </PieChart>
            </ResponsiveContainer>
            <div className="space-y-2">
              {donutData.map((d, i) => (
                <div key={d.name} className="flex items-center gap-2 text-xs text-stone-500">
                  <span
                    className="w-2.5 h-2.5 rounded-sm inline-block"
                    style={{ background: DONUT_COLORS[i] }}
                  />
                  {d.name}
                  <span className="text-stone-800 font-medium ml-auto pl-4">{d.value}%</span>
                </div>
              ))}
            </div>
          </div>
        </ChartCard>
      </div>

      {/* Scatter chart – Cost per Request */}
      <ChartCard title="Cost per Request (µ$)" loading={loading}>
        {requests.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-sm text-stone-400">
            No request data available
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart margin={{ bottom: 16 }}>
              <CartesianGrid vertical={false} stroke="#F5F5F4" />
              <XAxis
                type="number"
                dataKey="x"
                name="Request #"
                tick={{ fill: '#A8A29E', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                label={{ value: 'Request #', position: 'insideBottom', offset: -4, fill: '#A8A29E', fontSize: 11 }}
                domain={[1, requests.length]}
                allowDuplicatedCategory={false}
                tickCount={Math.min(requests.length, 10)}
                tickFormatter={(v) => Math.round(v).toString()}
              />
              <YAxis
                type="number"
                dataKey="y"
                name="Cost (µ$)"
                tick={{ fill: '#A8A29E', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={44}
              />
              <ZAxis range={[50, 50]} />
              <Tooltip
                cursor={{ strokeDasharray: '3 3', stroke: '#E7E5E4' }}
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const d = payload[0].payload as { x: number; y: number; label: string; model: string }
                  const isHit = payload[0].stroke === '#16A34A'
                  return (
                    <div className="rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs shadow-lg space-y-1">
                      <p className="text-stone-400">Request #{d.x} &middot; {d.label}</p>
                      <p style={{ color: isHit ? '#16A34A' : '#9333EA' }}>
                        {isHit ? '● Cache Hit' : '● Cache Miss'}
                      </p>
                      <p className="text-stone-800 font-medium">
                        Cost: {d.y.toFixed(6)} µ$
                      </p>
                      <p className="text-stone-500">Model: {d.model}</p>
                    </div>
                  )
                }}
              />
              <Legend
                wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
                formatter={(value) => (
                  <span style={{ color: value === 'Cache Hit' ? '#16A34A' : '#9333EA', fontSize: 12 }}>
                    {value}
                  </span>
                )}
              />
              <Line
                data={scatterAllHits}
                dataKey="y"
                name="Cache Hit"
                stroke="#16A34A"
                strokeWidth={1.5}
                dot={{ r: 4, fill: '#16A34A', strokeWidth: 0 }}
                activeDot={{ r: 6, fill: '#16A34A', strokeWidth: 0 }}
                type="monotone"
                legendType="circle"
              />
              <Line
                data={scatterAllMisses}
                dataKey="y"
                name="Cache Miss"
                stroke="#9333EA"
                strokeWidth={1.5}
                dot={{ r: 4, fill: '#9333EA', strokeWidth: 0 }}
                activeDot={{ r: 6, fill: '#9333EA', strokeWidth: 0 }}
                type="monotone"
                legendType="circle"
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </ChartCard>
    </div>
  )
}
