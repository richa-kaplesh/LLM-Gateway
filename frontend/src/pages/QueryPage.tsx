import { useState, useRef, useEffect } from 'react'
import { Send, Clock, DollarSign, Zap, CheckCircle2, Circle, Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { postQuery, type QueryResponse } from '@/lib/api'

interface HistoryEntry {
  id: string
  user_id: string
  query: string
  response: QueryResponse
  timestamp: Date
}

function ResponseCard({ entry }: { entry: HistoryEntry }) {
  const modelVariant = entry.response.model_used?.toLowerCase().includes('groq') ? 'groq' : 'gemini'
  const complexityVariant =
    entry.response.complexity?.toLowerCase() === 'simple' ? 'simple' : 'complex'

  return (
    <div className="rounded-lg border border-[#1f1f1f] bg-[#0f0f0f] p-4 space-y-3">
      {/* Query bubble */}
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-[#1a1a1a] border border-[#262626] px-3.5 py-2.5 text-sm text-[#ededed]">
          {entry.query}
        </div>
      </div>

      {/* Answer bubble */}
      <div className="flex justify-start">
        <div className="max-w-[85%] space-y-3">
          <div className="rounded-lg bg-[#111] border border-[#1f1f1f] px-3.5 py-2.5 text-sm text-[#ededed] leading-relaxed">
            {entry.response.answer}
          </div>

          {/* Metadata row */}
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={modelVariant}>{entry.response.model_used}</Badge>
            <Badge variant={complexityVariant}>{entry.response.complexity}</Badge>
            <span className="flex items-center gap-1 text-xs text-[#888]">
              <DollarSign className="w-3 h-3" />
              {entry.response.cost_usd < 0.000001
                ? entry.response.cost_usd.toExponential(2)
                : entry.response.cost_usd.toFixed(6)}
            </span>
            <span className="flex items-center gap-1 text-xs text-[#888]">
              <Clock className="w-3 h-3" />
              {entry.response.latency_ms.toFixed(0)} ms
            </span>
            {entry.response.cache_hit ? (
              <span className="flex items-center gap-1 text-xs text-green-400">
                <CheckCircle2 className="w-3 h-3" />
                Cached
              </span>
            ) : (
              <span className="flex items-center gap-1 text-xs text-[#555]">
                <Circle className="w-3 h-3" />
                Not cached
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export function QueryPage() {
  const [userId, setUserId] = useState('')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!query.trim()) return
    if (!userId.trim()) {
      toast.error('Please enter a User ID.')
      return
    }

    setLoading(true)
    try {
      const response = await postQuery({ user_id: userId.trim(), query: query.trim() })
      const entry: HistoryEntry = {
        id: crypto.randomUUID(),
        user_id: userId.trim(),
        query: query.trim(),
        response,
        timestamp: new Date(),
      }
      setHistory((prev) => [...prev.slice(-9), entry])
      setQuery('')
      toast.success('Response received.')
    } catch (err) {
      toast.error((err as Error).message || 'Failed to reach the gateway.')
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      handleSubmit(e as unknown as React.FormEvent)
    }
  }

  return (
    <div className="flex flex-col h-full max-w-3xl mx-auto w-full px-4 md:px-6">
      {/* Page header */}
      <div className="py-6 border-b border-[#1f1f1f]">
        <h1 className="text-lg font-semibold text-[#ededed]">Query Interface</h1>
        <p className="text-sm text-[#888] mt-0.5">Send queries to the LLM Gateway</p>
      </div>

      {/* Conversation history */}
      <div className="flex-1 overflow-y-auto py-4 space-y-3 min-h-0">
        {history.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center py-16">
            <div className="w-12 h-12 rounded-xl bg-white/5 border border-[#262626] flex items-center justify-center mb-4">
              <Zap className="w-5 h-5 text-[#555]" />
            </div>
            <p className="text-sm font-medium text-[#555]">No queries yet</p>
            <p className="text-xs text-[#333] mt-1">Submit a query below to get started</p>
          </div>
        ) : (
          history.map((entry) => <ResponseCard key={entry.id} entry={entry} />)
        )}
        <div ref={bottomRef} />
      </div>

      {/* History table */}
      {history.length > 0 && (
        <div className="py-4 border-t border-[#1f1f1f]">
          <h2 className="text-xs font-medium text-[#555] uppercase tracking-wider mb-3">
            Request History
          </h2>
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[#1f1f1f]">
                    {['User', 'Query', 'Model', 'Cost', 'Latency', 'Cache'].map((h) => (
                      <th
                        key={h}
                        className="text-left px-4 py-2.5 text-[#555] font-medium"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...history].reverse().map((entry) => (
                    <tr
                      key={entry.id}
                      className="border-b border-[#1a1a1a] last:border-0 hover:bg-white/2 transition-colors"
                    >
                      <td className="px-4 py-2.5 text-[#888] font-mono">{entry.user_id}</td>
                      <td className="px-4 py-2.5 text-[#ededed] max-w-[180px] truncate">
                        {entry.query}
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge
                          variant={
                            entry.response.model_used?.toLowerCase().includes('groq')
                              ? 'groq'
                              : 'gemini'
                          }
                        >
                          {entry.response.model_used}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5 text-[#888] font-mono">
                        ${entry.response.cost_usd.toFixed(6)}
                      </td>
                      <td className="px-4 py-2.5 text-[#888]">
                        {entry.response.latency_ms.toFixed(0)} ms
                      </td>
                      <td className="px-4 py-2.5">
                        {entry.response.cache_hit ? (
                          <span className="inline-flex items-center gap-1 text-green-400">
                            <span className="w-1.5 h-1.5 rounded-full bg-green-400 inline-block" />
                            Hit
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[#555]">
                            <span className="w-1.5 h-1.5 rounded-full bg-[#333] inline-block" />
                            Miss
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}

      {/* Input form */}
      <div className="py-4 border-t border-[#1f1f1f]">
        <form onSubmit={handleSubmit} className="space-y-3">
          <Input
            placeholder="User ID"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            className="w-full md:w-48"
          />
          <div className="flex gap-2 items-end">
            <Textarea
              placeholder="Type your query… (Ctrl+Enter to send)"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={3}
              className="flex-1"
            />
            <Button type="submit" disabled={loading || !query.trim() || !userId.trim()} className="h-[74px] px-4">
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
