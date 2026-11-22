import { useEffect, useState } from 'react'

const WAKING_AFTER_MS = 4_000
const GIVE_UP_AFTER_MS = 60_000

type Stage = 'early' | 'waking' | 'timeout'

export function useColdStart(pending: boolean, attempt: number) {
  const [mark, setMark] = useState<{ attempt: number; stage: Stage }>({ attempt, stage: 'early' })

  useEffect(() => {
    if (!pending) return
    const waking = window.setTimeout(() => setMark({ attempt, stage: 'waking' }), WAKING_AFTER_MS)
    const giveUp = window.setTimeout(() => setMark({ attempt, stage: 'timeout' }), GIVE_UP_AFTER_MS)
    return () => {
      window.clearTimeout(waking)
      window.clearTimeout(giveUp)
    }
  }, [pending, attempt])

  const stage: Stage = pending && mark.attempt === attempt ? mark.stage : 'early'
  return { waking: stage === 'waking', timedOut: stage === 'timeout' }
}
