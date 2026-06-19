import numpy as np
from sentence_transformers import SentenceTransformer
from app.models.schemas import GatewayResponse
from app.core.config import get_settings

settings = get_settings()

# Load embedding model once at startup
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")


class SemanticCache:
    def __init__(self):
        self.queries: list[str] = []
        self.embeddings: list[np.ndarray] = []
        self.responses: list[GatewayResponse] = []

    def _get_embedding(self, text: str) -> np.ndarray:
        return embedding_model.encode(text, normalize_embeddings=True)

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    def get(self, query: str) -> GatewayResponse | None:
        if not self.queries:
            return None

        query_embedding = self._get_embedding(query)

        similarities = [
            self._cosine_similarity(query_embedding, cached_emb)
            for cached_emb in self.embeddings
        ]

        max_similarity = max(similarities)
        max_index = similarities.index(max_similarity)

        if max_similarity >= settings.CACHE_SIMILARITY_THRESHOLD:
            cached_response = self.responses[max_index]
            return GatewayResponse(
                response=cached_response.response,
                model_used=cached_response.model_used,
                complexity=cached_response.complexity,
                cost_usd=0.0,
                latency_ms=0.0,
                cache_hit=True
            )

        return None

    def set(self, query: str, response: GatewayResponse) -> None:
        if len(self.queries) >= settings.CACHE_MAX_SIZE:
            self.queries.pop(0)
            self.embeddings.pop(0)
            self.responses.pop(0)

        self.queries.append(query)
        self.embeddings.append(self._get_embedding(query))
        self.responses.append(response)


# Single instance shared across entire app
cache = SemanticCache()