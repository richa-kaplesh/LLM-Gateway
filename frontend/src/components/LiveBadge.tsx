import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export type LiveTone = 'live' | 'idle' | 'error'

const TONES: Record<LiveTone, { pill: string; dot: string }> = {
  live: { pill: 'border-emerald-200 bg-emerald-50 text-emerald-700', dot: 'bg-emerald-500' },
  idle: { pill: 'border-stone-200 bg-white text-stone-500', dot: 'bg-stone-400' },
  error: { pill: 'border-red-200 bg-red-50 text-red-700', dot: 'bg-red-500' },
}

export function LiveBadge({ tone, label, children }: { tone: LiveTone; label: string; children?: ReactNode }) {
  const t = TONES[tone]
  return (
    <span className={cn('inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs', t.pill)}>
      <span className="relative flex h-2 w-2">
        {tone === 'live' && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
        )}
        <span className={cn('relative inline-flex h-2 w-2 rounded-full', t.dot)} />
      </span>
      {label}
      {children}
    </span>
  )
}
