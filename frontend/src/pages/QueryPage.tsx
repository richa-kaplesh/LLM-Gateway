import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Send, Clock, DollarSign, Zap, CheckCircle2, Circle, Loader2, Wrench } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { postQuery, type QueryResponse, type GatewayMessage } from '@/lib/api'

interface HistoryEntry {
  id: string
  user_id: string
  query: string
  response: QueryResponse
  timestamp: Date
}

function ResponseCard({ entry }: { entry: HistoryEntry }) {
  const modelVariant = entry.response.model_used?.toLowerCase().includes('groq') ? 'groq' : 'gemini'
  const hasToolCalls = entry.response.tool_calls && entry.response.tool_calls.length > 0

  return (
    <div className="rounded-xl border border-stone-200 bg-white p-4 space-y-3 shadow-sm animate-card-in">
      {/* Query bubble — user */}
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-xl rounded-tr-sm bg-[#F0EDE8] border border-stone-200 px-3.5 py-2.5 text-sm text-stone-800">
          {entry.query}
        </div>
      </div>

      {/* Response bubble — assistant */}
      <div className="flex justify-start">
        <div className="max-w-[85%] space-y-3">
          <div className="rounded-xl rounded-tl-sm bg-stone-50 border border-stone-200 px-3.5 py-2.5 leading-relaxed overflow-x-auto">
            {hasToolCalls ? (
              <div className="flex flex-wrap gap-2">
                {entry.response.tool_calls!.map((tc) => (
                  <span
                    key={tc.id}
                    className="inline-flex items-center gap-1.5 rounded-md border border-violet-200 bg-violet-50 px-2 py-1 text-xs font-medium text-violet-800"
                  >
                    <Wrench className="w-3 h-3" />
                    {tc.function.name}
                  </span>
                ))}
              </div>
            ) : (
              <div className="prose-light">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {entry.response.content ?? '*(empty response)*'}
                </ReactMarkdown>
              </div>
            )}
          </div>

          {/* Metadata row */}
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={modelVariant}>{entry.response.model_used}</Badge>
            {hasToolCalls && <Badge variant="tool">Tool call</Badge>}
            <span className="flex items-center gap-1 text-xs text-stone-400">
              <DollarSign className="w-3 h-3" />
              {entry.response.cost_usd < 0.000001
                ? entry.response.cost_usd.toExponential(2)
                : entry.response.cost_usd.toFixed(6)}
            </span>
            <span className="flex items-center gap-1 text-xs text-stone-400">
              <Clock className="w-3 h-3" />
              {entry.response.latency_ms.toFixed(0)} ms
            </span>
            {entry.response.cache_hit ? (
              <span className="flex items-center gap-1 text-xs text-green-700">
                <CheckCircle2 className="w-3 h-3" />
                Cached
              </span>
            ) : (
              <span className="flex items-center gap-1 text-xs text-stone-400">
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
  // One conversation_id per page mount — reused across all messages in this session
  const [conversationId] = useState(() => crypto.randomUUID())
  const [userId, setUserId] = useState('')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  // Full message thread for the backend
  const [messages, setMessages] = useState<GatewayMessage[]>([])
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

    const userMessage: GatewayMessage = { role: 'user', content: query.trim() }
    const nextMessages = [...messages, userMessage]

    setLoading(true)
    try {
      const response = await postQuery({
        conversation_id: conversationId,
        user_id: userId.trim(),
        messages: nextMessages,
      })

      // Append assistant turn to the running thread
      const assistantMessage: GatewayMessage = {
        role: 'assistant',
        content: response.content ?? '',
      }
      setMessages([...nextMessages, assistantMessage])

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
      <div className="py-5 border-b border-stone-200">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-stone-900">Query Interface</h1>
            <p className="text-sm text-stone-500 mt-0.5">Send queries to the LLM Gateway</p>
          </div>
          {/* User ID — session-level setting */}
          <div className="flex flex-col gap-1 shrink-0">
            <label className="text-xs font-medium text-stone-400 uppercase tracking-widest">User ID</label>
            <Input
              placeholder="e.g. alice"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              className="w-36 h-8 text-xs"
            />
          </div>
        </div>
      </div>

      {/* Conversation history */}
      <div className="flex-1 overflow-y-auto py-4 space-y-3 min-h-0">
        {history.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center py-16">
            <div className="w-12 h-12 rounded-xl bg-amber-50 border border-amber-200 flex items-center justify-center mb-4">
              <Zap className="w-5 h-5 text-amber-600" />
            </div>
            <p className="text-sm font-medium text-stone-500">No queries yet</p>
            <p className="text-xs text-stone-400 mt-1">Submit a query below to get started</p>
          </div>
        ) : (
          history.map((entry) => <ResponseCard key={entry.id} entry={entry} />)
        )}
        <div ref={bottomRef} />
      </div>

      {/* History table */}
      {history.length > 0 && (
        <div className="py-4 border-t border-stone-200">
          <h2 className="text-xs font-medium text-stone-400 uppercase tracking-widest mb-3">
            Request History
          </h2>
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-stone-100">
                    {['User', 'Query', 'Model', 'Cost', 'Latency', 'Cache'].map((h) => (
                      <th
                        key={h}
                        className="text-left px-4 py-2.5 text-stone-400 font-medium uppercase tracking-wide text-[11px]"
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
                      className="border-b border-stone-50 last:border-0 hover:bg-stone-50 transition-colors duration-100"
                    >
                      <td className="px-4 py-2.5 text-stone-500 font-mono">{entry.user_id}</td>
                      <td className="px-4 py-2.5 text-stone-800 max-w-[180px] truncate">
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
                      <td className="px-4 py-2.5 text-stone-500 font-mono">
                        ${entry.response.cost_usd.toFixed(6)}
                      </td>
                      <td className="px-4 py-2.5 text-stone-500">
                        {entry.response.latency_ms.toFixed(0)} ms
                      </td>
                      <td className="px-4 py-2.5">
                        {entry.response.cache_hit ? (
                          <span className="inline-flex items-center gap-1 text-green-700">
                            <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block" />
                            Hit
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-stone-400">
                            <span className="w-1.5 h-1.5 rounded-full bg-stone-300 inline-block" />
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
      <div className="py-4 border-t border-stone-200">
        <form onSubmit={handleSubmit} className="space-y-2">
          <div className="flex gap-2 items-end">
            <Textarea
              placeholder="Type your query… (Ctrl+Enter to send)"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={3}
              className="flex-1"
            />
            <Button
              type="submit"
              disabled={loading || !query.trim() || !userId.trim()}
              className={`h-[76px] px-5 transition-all duration-150 ${loading ? 'opacity-80' : ''}`}
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </Button>
          </div>
          <p className="text-[11px] text-stone-400 px-1">
            Session ID: <span className="font-mono">{conversationId.slice(0, 8)}…</span>
          </p>
        </form>
      </div>
    </div>
  )
}
