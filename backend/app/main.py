from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.models.schemas import GatewayRequest, GatewayResponse, CostSummary, HealthCheck
from app.router.router import route, estimate_tokens
from app.tracker.tracker import tracker
from app.core.config import get_settings
from app.core.db import init_pool, close_pool
import groq
from google import genai
from app.experiments.schema import ExperimentRun
from app.experiments.store import load_runs, save_run
from app.router.circuit_breaker import breakers
import logging
from app.clients.exceptions import InvalidRequestError, TooLongError, AllProvidersRateLimitedError

log = logging.getLogger("gateway")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title=settings.APP_NAME, version=settings.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get("/health", response_model=HealthCheck)
async def health_check():
    groq_available = True
    gemini_available = True

    try:
        groq_client = groq.Groq(api_key=settings.GROQ_API_KEY)
        groq_client.models.list()
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
        await tracker.log(
            request.user_id, request.conversation_id, response, status="success",
            cache_scope=request.cache_scope,
            estimated_tokens=estimate_tokens(request.messages),
        )
        return response

    except InvalidRequestError as e:
        await tracker.log(request.user_id, request.conversation_id, None,
                           status="error", error_type="InvalidRequestError",
                           cache_scope=request.cache_scope,
                           estimated_tokens=estimate_tokens(request.messages))
        raise HTTPException(status_code=400, detail=str(e))

    except TooLongError as e:
        await tracker.log(request.user_id, request.conversation_id, None,
                           status="error", error_type="TooLongError",
                           cache_scope=request.cache_scope,
                           estimated_tokens=estimate_tokens(request.messages))
        raise HTTPException(status_code=413, detail=str(e))

    except AllProvidersRateLimitedError as e:
        await tracker.log(request.user_id, request.conversation_id, None,
                           status="error", error_type="AllProvidersRateLimitedError",
                           cache_scope=request.cache_scope,
                           estimated_tokens=estimate_tokens(request.messages))
        raise HTTPException(status_code=429, detail=str(e))

    except Exception as e:
        log.error(f"/query failed: {e}", exc_info=True)
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