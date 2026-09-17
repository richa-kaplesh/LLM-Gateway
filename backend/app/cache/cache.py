import numpy as np
from google import genai
from app.models.schemas import GatewayRequest, GatewayResponse
from app.core.config import get_settings

settings = get_settings()
embed_client = genai.Client(api_key=settings.GEMINI_API_KEY)
EMBEDDING_MODEL = "text-embedding-004"


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

    async def _get_embedding(self, text: str) -> np.ndarray:
        response = await embed_client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
        )
        vector = np.array(response.embeddings[0].values)
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 0 else vector

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        return float(np.dot(a, b))

    async def get(self, request: GatewayRequest) -> GatewayResponse | None:
        if not self.queries:
            return None
        query_text = _extract_query_text(request)
        if not query_text:
            return None

        query_embedding = await self._get_embedding(query_text)
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

    async def set(self, request: GatewayRequest, response: GatewayResponse) -> None:
        query_text = _extract_query_text(request)
        if not query_text:
            return
        if len(self.queries) >= settings.CACHE_MAX_SIZE:
            self.queries.pop(0)
            self.embeddings.pop(0)
            self.responses.pop(0)
        self.queries.append(query_text)
        self.embeddings.append(await self._get_embedding(query_text))
        self.responses.append(response)


cache = SemanticCache()