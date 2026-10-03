import time
import httpx
import numpy as np
from dataclasses import dataclass
from collections import OrderedDict
from app.models.schemas import GatewayRequest, GatewayResponse
from app.core.config import get_settings

settings = get_settings()

_EMBED_URL = "https://api.jina.ai/v1/embeddings"
_MODEL = "jina-embeddings-v3"
_DIM = 1024

_headers = {
    "Authorization": f"Bearer {settings.JINA_API_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def _extract_query_text(request: GatewayRequest) -> str:
    for msg in reversed(request.messages):
        if msg["role"] == "user":
            return msg["content"]
    return ""


@dataclass
class EmbedResult:
    vector: np.ndarray
    tokens: int
    latency_ms: float
    cost_usd: float


@dataclass
class LookupResult:
    hit: GatewayResponse | None
    embedding: EmbedResult | None      # reused by set() so a miss embeds only once
    max_similarity: float | None = None


# One shared async client: no per-request connection setup, never blocks the event loop.
_http = httpx.AsyncClient(timeout=30.0)


async def close_http() -> None:
    await _http.aclose()


async def embed(text: str) -> EmbedResult:
    payload = {"model": _MODEL, "task": "text-matching", "dimensions": _DIM, "input": [text]}
    t0 = time.perf_counter()
    resp = await _http.post(_EMBED_URL, headers=_headers, json=payload)
    latency_ms = (time.perf_counter() - t0) * 1000
    if resp.status_code != 200:
        raise RuntimeError(f"Jina embed API error {resp.status_code}: {resp.text[:400]}")
    body = resp.json()
    vector = np.array(body["data"][0]["embedding"], dtype=np.float32)
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
    tokens = int((body.get("usage") or {}).get("total_tokens", 0))   # real count from Jina
    cost = (tokens / 1_000_000) * settings.JINA_COST_PER_MILLION
    return EmbedResult(vector=vector, tokens=tokens, latency_ms=latency_ms, cost_usd=cost)


class SemanticCache:
    def __init__(self):
        self.queries: list[str] = []
        self.embeddings: list[np.ndarray] = []
        self.responses: list[GatewayResponse] = []

    async def get(self, request: GatewayRequest) -> LookupResult:
        query_text = _extract_query_text(request)
        if not query_text:
            return LookupResult(hit=None, embedding=None)

        emb = await embed(query_text)          # always embed once; set() reuses it on a miss
        if not self.queries:
            return LookupResult(hit=None, embedding=emb)

        t0 = time.perf_counter()
        sims = [float(np.dot(emb.vector, e)) for e in self.embeddings]
        max_sim = max(sims)
        idx = sims.index(max_sim)
        lookup_ms = (time.perf_counter() - t0) * 1000

        if max_sim >= settings.CACHE_SIMILARITY_THRESHOLD:
            cached = self.responses[idx]
            return LookupResult(
                hit=GatewayResponse(
                    content=cached.content, tool_calls=cached.tool_calls,
                    finish_reason=cached.finish_reason, model_used=cached.model_used,
                    provider_used=cached.provider_used,
                    # real overhead of serving a hit: the embedding call + the scan
                    cost_usd=emb.cost_usd,
                    latency_ms=emb.latency_ms + lookup_ms,
                    embed_latency_ms=emb.latency_ms, embed_cost_usd=emb.cost_usd,
                    cache_hit=True, cache_scope=request.cache_scope,
                ),
                embedding=emb, max_similarity=max_sim,
            )
        return LookupResult(hit=None, embedding=emb, max_similarity=max_sim)

    def set(self, request: GatewayRequest, response: GatewayResponse, embedding: EmbedResult | None) -> None:
        query_text = _extract_query_text(request)
        if not query_text or embedding is None:
            return
        if len(self.queries) >= settings.CACHE_MAX_SIZE:
            self.queries.pop(0)
            self.embeddings.pop(0)
            self.responses.pop(0)
        self.queries.append(query_text)
        self.embeddings.append(embedding.vector)
        self.responses.append(response)


# ── Scoping: conversation-private caches vs one shared global cache ────────
conversation_caches: OrderedDict[str, SemanticCache] = OrderedDict()
global_cache = SemanticCache()

MAX_TRACKED_CONVERSATIONS = 500


def get_cache_for(request: GatewayRequest) -> SemanticCache | None:
    if request.cache_scope == "conversation":
        conv_id = request.conversation_id
        if conv_id in conversation_caches:
            conversation_caches.move_to_end(conv_id)
        else:
            conversation_caches[conv_id] = SemanticCache()
            if len(conversation_caches) > MAX_TRACKED_CONVERSATIONS:
                conversation_caches.popitem(last=False)
        return conversation_caches[conv_id]

    if request.cache_scope == "global":
        return global_cache

    return None