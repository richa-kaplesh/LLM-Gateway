from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from app.models.schemas import GatewayRequest, GatewayResponse, CostSummary, HealthCheck
from app.router.router import route, estimate_request_tokens
from app.tracker.tracker import tracker
from app.core.config import get_settings
from app.core.db import init_pool, close_pool
from app.cache.cache import close_http
import groq
from google import genai
from app.experiments.schema import ExperimentRun
from app.experiments.store import load_runs, save_run
from app.router.circuit_breaker import breakers
import logging
from app.clients.exceptions import (
    InvalidRequestError, TooLongError, AllProvidersRateLimitedError, AllProvidersUnavailableError,
)
import asyncio
import math
import time
import uuid
from app.core.logging_setup import setup_logging, request_id_var

setup_logging()

# uvicorn puts its own plain-text handlers on these loggers, which would break
# "every line is JSON". Route them through our root handler instead.
for _name in ("uvicorn", "uvicorn.error"):
    _lg = logging.getLogger(_name)
    _lg.handlers.clear()
    _lg.propagate = True
logging.getLogger("uvicorn.access").disabled = True   # the middleware below logs every request
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs every outbound call at INFO

log = logging.getLogger(__name__)
http_log = logging.getLogger("gateway.http")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_http()
    await close_pool()


app = FastAPI(title=settings.APP_NAME, version=settings.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.middleware("http")
async def request_context(request: Request, call_next):
    req_id = (request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12])[:64]
    token = request_id_var.set(req_id)
    start = time.perf_counter()
    http_log.info("request started: %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        http_log.info("request finished: %s %s -> %d in %.1f ms",
                      request.method, request.url.path, response.status_code,
                      (time.perf_counter() - start) * 1000)
        return response
    except Exception:
        http_log.error("request crashed: %s %s after %.1f ms",
                       request.method, request.url.path,
                       (time.perf_counter() - start) * 1000, exc_info=True)
        raise
    finally:
        request_id_var.reset(token)


@app.get("/health", response_model=HealthCheck)
async def health_check():
    groq_available = True
    gemini_available = True

    try:
        groq_client = groq.AsyncGroq(api_key=settings.groq_keys()[0])
        await groq_client.models.list()
    except Exception:
        groq_available = False

    try:
        genai.Client(api_key=settings.gemini_keys()[0])
    except Exception:
        gemini_available = False

    return HealthCheck(status="ok", groq_available=groq_available, gemini_available=gemini_available)


DB_TIMEOUT_SECONDS = 2.0


async def _track(request: GatewayRequest, response, status: str, error_type: str | None = None) -> None:
    """Best-effort request logging. A database problem (Neon suspended, connection
    reset) must never turn a good answer into a 500, or hang the response."""
    try:
        await asyncio.wait_for(
            tracker.log(
                request.user_id, request.conversation_id, response, status=status,
                error_type=error_type, cache_scope=request.cache_scope,
                estimated_tokens=estimate_request_tokens(request),
            ),
            DB_TIMEOUT_SECONDS,
        )
    except Exception as e:
        log.warning("could not persist request log: %s: %s", type(e).__name__, e)


def _retry_headers(retry_after: float | None) -> dict | None:
    if retry_after is None:
        return None
    return {"Retry-After": str(max(1, math.ceil(retry_after)))}


@app.post("/query", response_model=GatewayResponse)
async def handle_query(request: GatewayRequest):
    try:
        response = await route(request)
    except InvalidRequestError as e:
        log.info("/query rejected (invalid request): %s", e)
        await _track(request, None, "error", "InvalidRequestError")
        raise HTTPException(status_code=400, detail=str(e))

    except TooLongError as e:
        log.info("/query rejected (too long): %s", e)
        await _track(request, None, "error", "TooLongError")
        raise HTTPException(status_code=413, detail=str(e))

    except AllProvidersRateLimitedError as e:
        log.warning("/query rejected (all providers rate-limited): %s", e)
        await _track(request, None, "error", "AllProvidersRateLimitedError")
        raise HTTPException(status_code=429, detail=str(e), headers=_retry_headers(e.retry_after))

    except AllProvidersUnavailableError as e:
        # a dependency problem, not a gateway bug: 503, one log line, no traceback
        log.error("/query failed (no provider available): %s", e)
        await _track(request, None, "error", "AllProvidersUnavailableError")
        raise HTTPException(status_code=503, detail=str(e), headers=_retry_headers(e.retry_after))

    except Exception as e:
        log.error("/query failed: %s", e, exc_info=True)
        await _track(request, None, "error", type(e).__name__)
        raise HTTPException(status_code=500, detail=str(e))

    log.info("/query ok: provider=%s model=%s fallback=%s cache_hit=%s latency_ms=%.0f cost_usd=%.6f",
             response.provider_used, response.model_used, response.was_fallback,
             response.cache_hit, response.latency_ms, response.cost_usd)
    await _track(request, response, "success")
    return response


@app.get("/stats/global")
async def global_stats():
    return await tracker.get_global_stats()


@app.get("/stats/user/{user_id}", response_model=CostSummary)
async def user_stats(user_id: str):
    stats = await tracker.get_user_stats(user_id)
    if stats is None:
        raise HTTPException(status_code=404, detail=f"No requests found for user '{user_id}'")
    return stats


@app.get("/stats/requests")
async def request_history(limit: int = 200):
    return await tracker.get_recent_requests(limit)


@app.get("/experiments")
async def get_experiments():
    return load_runs()


@app.post("/experiments")
async def log_experiment(run: ExperimentRun):
    return save_run(run)


@app.get("/breakers")
async def breaker_status():
    from app.router.router import _key_managers
    result = {}
    for name, b in breakers.items():
        result[name] = {
            **b.snapshot(),
            "keys": _key_managers[name].status_summary(),
        }
    return result