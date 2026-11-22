export function formatLatency(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

export function formatPercent(pct: number): string {
  return `${pct.toFixed(1)}%`
}

export function formatMoney(usd: number): string {
  if (usd > 0 && usd < 0.01) return '< $0.01'
  return `$${usd.toFixed(2)}`
}

export function formatCount(n: number): string {
  return Math.round(n).toLocaleString('en-US')
}

export function formatWait(seconds: number | null | undefined): string {
  if (seconds == null) return 'unknown'
  if (seconds <= 0) return 'now'
  if (seconds < 60) return `in ${Math.ceil(seconds)} s`
  if (seconds < 3600) return `in ${Math.ceil(seconds / 60)} min`
  return `in ${(seconds / 3600).toFixed(1)} h`
}

const DAY = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' })
const TIME = new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit', hour12: false })

export function formatWindow(since: string | null, until: string | null): string {
  const a = since ? new Date(since) : null
  const b = until ? new Date(until) : null
  if (a && b) {
    if (a.toDateString() === b.toDateString()) return `${DAY.format(a)}, ${TIME.format(a)}–${TIME.format(b)}`
    return `${DAY.format(a)} – ${DAY.format(b)}`
  }
  if (a) return `since ${DAY.format(a)}`
  if (b) return `until ${DAY.format(b)}`
  return 'all time'
}
