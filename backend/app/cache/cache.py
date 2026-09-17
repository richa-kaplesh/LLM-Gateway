import httpx
import numpy as np
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


class SemanticCache:
    def __init__(self):
        self.queries: list[str] = []
        self.embeddings: list[np.ndarray] = []
        self.responses: list[GatewayResponse] = []

    def _get_embedding(self, text: str) -> np.ndarray:
        # "text-matching" — comparing query-to-query (symmetric), not
        # query-to-document like QueryMind's retrieval.query/retrieval.passage
        payload = {"model": _MODEL, "task": "text-matching", "dimensions": _DIM, "input": [text]}
        resp = httpx.post(_EMBED_URL, headers=_headers, json=payload, timeout=30.0)
        if resp.status_code != 200:
            raise RuntimeError(f"Jina embed API error {resp.status_code}: {resp.text[:400]}")
        vector = np.array(resp.json()["data"][0]["embedding"], dtype=np.float32)
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 0 else vector

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    def get(self, request: GatewayRequest) -> GatewayResponse | None:
        if not self.queries:
            return None
        query_text = _extract_query_text(request)
        if not query_text:
            return None

        query_embedding = self._get_embedding(query_text)
        similarities = [self._cosine_similarity(query_embedding, e) for e in self.embeddings]
        max_similarity = max(similarities)
        max_index = similarities.index(max_similarity)

        if max_similarity >= settings.CACHE_SIMILARITY_THRESHOLD:
            cached = self.responses[max_index]
            return GatewayResponse(
                content=cached.content, tool_calls=cached.tool_calls,
                finish_reason=cached.finish_reason, model_used=cached.model_used,
                cost_usd=0.0, latency_ms=0.0, cache_hit=True
            )
        return None

    def set(self, request: GatewayRequest, response: GatewayResponse) -> None:
        query_text = _extract_query_text(request)
        if not query_text:
            return
        if len(self.queries) >= settings.CACHE_MAX_SIZE:
            self.queries.pop(0)
            self.embeddings.pop(0)
            self.responses.pop(0)
        self.queries.append(query_text)
        self.embeddings.append(self._get_embedding(query_text))
        self.responses.append(response)


cache = SemanticCache()