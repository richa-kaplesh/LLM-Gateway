import { useEffect, useState, useCallback } from 'react'
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
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
import { fetchGlobalStats, type GlobalStats } from '@/lib/api'

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
    <div className="rounded-md border border-[#262626] bg-[#0f0f0f] px-3 py-2 text-xs shadow-xl">
      {label && <p className="text-[#555] mb-1.5">{label}</p>}
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }} className="flex gap-2 items-center">
          <span className="text-[#888]">{p.name}:</span>
          <span className="font-medium">{p.value}</span>
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
}
function StatCard({ title, value, icon, loading }: StatCardProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>{title}</CardTitle>
          <div className="text-[#333]">{icon}</div>
        </div>
        {loading ? <Skeleton className="h-8 w-28 mt-1" /> : <CardValue>{value}</CardValue>}
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
        <CardTitle className="text-[#888] text-sm font-medium">{title}</CardTitle>
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
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true)
    else setLoading(true)
    try {
      const data = await fetchGlobalStats()
      setStats(data)
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

  const costData = (stats?.requests_over_time ?? []).map((r, i) => ({
    index: i + 1,
    cost: parseFloat((r.cost_usd * 1_000_000).toFixed(4)),
    label: r.timestamp ? new Date(r.timestamp).toLocaleTimeString() : `#${i + 1}`,
  }))

  const hitRate = stats ? Math.round(stats.cache_hit_rate * 100) : 0
  const donutData = [
    { name: 'Cache Hit', value: hitRate },
    { name: 'Miss', value: 100 - hitRate },
  ]
  const DONUT_COLORS = ['#22c55e', '#1f1f1f']

  return (
    <div className="p-4 md:p-6 space-y-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-[#ededed]">Analytics Dashboard</h1>
          <p className="text-sm text-[#888] mt-0.5">Gateway-wide statistics</p>
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
          value={stats ? `$${stats.total_cost_usd.toFixed(4)}` : '—'}
          icon={<DollarSign className="w-4 h-4" />}
          loading={loading}
        />
        <StatCard
          title="Cost Saved"
          value={stats ? `$${stats.total_cost_saved_usd.toFixed(4)}` : '—'}
          icon={<TrendingUp className="w-4 h-4" />}
          loading={loading}
        />
        <StatCard
          title="Cache Hit Rate"
          value={stats ? `${hitRate}%` : '—'}
          icon={<Zap className="w-4 h-4" />}
          loading={loading}
        />
      </div>

      {/* Charts row 1 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {/* Bar chart */}
        <ChartCard title="Requests by Model" loading={loading}>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={modelData} barSize={36}>
              <CartesianGrid vertical={false} stroke="#1a1a1a" />
              <XAxis
                dataKey="name"
                tick={{ fill: '#555', fontSize: 12 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#555', fontSize: 12 }}
                axisLine={false}
                tickLine={false}
                width={36}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(255,255,255,0.03)' }} />
              <Bar dataKey="requests" radius={[4, 4, 0, 0]}>
                <Cell fill="#a78bfa" />
                <Cell fill="#60a5fa" />
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
                  className="fill-[#ededed]"
                  style={{ fontSize: 20, fontWeight: 600, fill: '#ededed' }}
                >
                  {hitRate}%
                </text>
              </PieChart>
            </ResponsiveContainer>
            <div className="space-y-2">
              {donutData.map((d, i) => (
                <div key={d.name} className="flex items-center gap-2 text-xs text-[#888]">
                  <span
                    className="w-2.5 h-2.5 rounded-sm inline-block"
                    style={{ background: DONUT_COLORS[i] }}
                  />
                  {d.name}
                  <span className="text-[#ededed] font-medium ml-auto pl-4">{d.value}%</span>
                </div>
              ))}
            </div>
          </div>
        </ChartCard>
      </div>

      {/* Line chart */}
      <ChartCard title="Cost per Request (µ$)" loading={loading}>
        {costData.length === 0 ? (
          <div className="flex items-center justify-center h-48 text-sm text-[#555]">
            No request data available
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={costData}>
              <CartesianGrid vertical={false} stroke="#1a1a1a" />
              <XAxis
                dataKey="label"
                tick={{ fill: '#555', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fill: '#555', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={40}
                tickFormatter={(v) => `${v}`}
              />
              <Tooltip
                content={({ active, payload, label }) => (
                  <ChartTooltip
                    active={active}
                    label={String(label ?? '')}
                    payload={payload?.map((p) => ({
                      name: 'Cost (µ$)',
                      value: p.value as number,
                      color: '#ededed',
                    }))}
                  />
                )}
                cursor={{ stroke: '#333' }}
              />
              <Legend
                wrapperStyle={{ fontSize: 12, color: '#555' }}
                formatter={() => 'Cost µ$'}
              />
              <Line
                type="monotone"
                dataKey="cost"
                stroke="#ededed"
                strokeWidth={1.5}
                dot={false}
                activeDot={{ r: 4, fill: '#ededed', strokeWidth: 0 }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </ChartCard>
    </div>
  )
}
