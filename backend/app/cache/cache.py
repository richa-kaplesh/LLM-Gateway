import numpy as np
from sentence_transformers import SentenceTransformer
from app.models.schemas import GatewayRequest, GatewayResponse
from app.core.config import get_settings

settings = get_settings()
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")


def _extract_query_text(request: GatewayRequest) -> str:
    """Cache key = most recent user message only, not full history."""
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
        return embedding_model.encode(text, normalize_embeddings=True)

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
                content=cached.content,
                tool_calls=cached.tool_calls,
                finish_reason=cached.finish_reason,
                model_used=cached.model_used,
                cost_usd=0.0,
                latency_ms=0.0,
                cache_hit=True
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