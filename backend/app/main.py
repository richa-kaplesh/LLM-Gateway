from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from app.models.schemas import GatewayRequest, GatewayResponse, CostSummary, HealthCheck
from app.router.router import route, estimate_tokens
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
from app.clients.exceptions import InvalidRequestError, TooLongError, AllProvidersRateLimitedError
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
        groq_client = groq.AsyncGroq(api_key=settings.GROQ_API_KEY)
        await groq_client.models.list()
    except Exception:
        groq_available = False

    try:
        genai.Client(api_key=settings.GEMINI_API_KEY)
    except Exception:
        gemini_available = False

    return HealthCheck(status="ok", groq_available=groq_available, gemini_available=gemini_available)


@app.post("/query", response_model=GatewayResponse)
async def handle_query(request: GatewayRequest):
    try:
        response = await route(request)
        log.info("/query ok: provider=%s model=%s fallback=%s cache_hit=%s latency_ms=%.0f cost_usd=%.6f",
                 response.provider_used, response.model_used, response.was_fallback,
                 response.cache_hit, response.latency_ms, response.cost_usd)
        await tracker.log(
            request.user_id, request.conversation_id, response, status="success",
            cache_scope=request.cache_scope,
            estimated_tokens=estimate_tokens(request.messages),
        )
        return response

    except InvalidRequestError as e:
        log.info("/query rejected (invalid request): %s", e)
        await tracker.log(request.user_id, request.conversation_id, None,
                           status="error", error_type="InvalidRequestError",
                           cache_scope=request.cache_scope,
                           estimated_tokens=estimate_tokens(request.messages))
        raise HTTPException(status_code=400, detail=str(e))

    except TooLongError as e:
        log.info("/query rejected (too long): %s", e)


        await tracker.log(request.user_id, request.conversation_id, None,
                           status="error", error_type="TooLongError",
                           cache_scope=request.cache_scope,
                           estimated_tokens=estimate_tokens(request.messages))
        raise HTTPException(status_code=413, detail=str(e))

    except AllProvidersRateLimitedError as e: 
        log.warning("/query rejected (all providers rate-limited): %s", e)


        await tracker.log(request.user_id, request.conversation_id, None,
                           status="error", error_type="AllProvidersRateLimitedError",
                           cache_scope=request.cache_scope,
                           estimated_tokens=estimate_tokens(request.messages))
        raise HTTPException(status_code=429, detail=str(e))

    except Exception as e:
        log.error("/query failed: %s", e, exc_info=True)
        await tracker.log(request.user_id, request.conversation_id, None,
                           status="error", error_type=type(e).__name__,
                           cache_scope=request.cache_scope,
                           estimated_tokens=estimate_tokens(request.messages))
        raise HTTPException(status_code=500, detail=str(e))

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
    return {name: b.snapshot() for name, b in breakers.items()}