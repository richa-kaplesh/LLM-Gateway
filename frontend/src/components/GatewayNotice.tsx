import { CloudOff, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function WakingNotice() {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 animate-card-in">
      <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />
      <p>Waking the server. The free tier sleeps when idle; this usually takes about 30 seconds.</p>
    </div>
  )
}

export function UnreachableNotice({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-4 rounded-xl border border-stone-200 bg-white px-6 py-12 text-center shadow-sm animate-card-in">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-red-200 bg-red-50">
        <CloudOff className="h-5 w-5 text-red-600" />
      </div>
      <p className="text-base font-medium text-stone-900">Couldn't reach the gateway.</p>
      <Button variant="outline" onClick={onRetry} className="gap-1.5">
        <RefreshCw className="h-3.5 w-3.5" />
        Retry
      </Button>
    </div>
  )
}
