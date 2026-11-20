from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import asyncio
import hmac
import logging
import math
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.cache.cache import close_http
from app.clients.adapters import close_adapters
from app.clients.exceptions import GatewayRejection, InvalidRequestError, TooLongError
from app.core.config import get_settings
from app.core.db import close_pool, has_pool, init_pool
from app.core.logging_setup import request_id_var, setup_logging
from app.core.registry import get_deployments
from app.experiments.schema import ExperimentRun
from app.experiments.store import load_runs, save_run
from app.models.schemas import CostSummary, GatewayRequest, GatewayResponse
from app.router import attempt_log
from app.router.router import estimate_request_tokens, route
from app.router.status import gateway_status
from app.state.store import close_store, get_store, init_store
from app.tracker import metrics as metrics_store
from app.tracker.tracker import tracker

setup_logging()

for _name in ("uvicorn", "uvicorn.error"):
    _lg = logging.getLogger(_name)
    _lg.handlers.clear()
    _lg.propagate = True
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger(__name__)
http_log = logging.getLogger("gateway.http")

settings = get_settings()
DB_TIMEOUT_SECONDS = 2.0
MAX_HISTORY_ROWS = 1000


@asynccontextmanager
async def lifespan(app: FastAPI):
    deployments = get_deployments()
    log.info("registry loaded: %d enabled deployments: %s", len(deployments), [d.id for d in deployments])
    await init_store(settings.REDIS_URL)
    if await init_pool():
        try:
            await metrics_store.ensure_tables()
        except Exception as e:
            log.warning("could not ensure metrics tables: %s: %s", type(e).__name__, e)
    else:
        log.warning("DATABASE_URL is empty: request history and metrics are disabled")
    if not settings.api_keys:
        log.warning("GATEWAY_API_KEYS is empty: POST endpoints are open to anyone")
    yield
    await close_adapters()
    await close_http()
    await close_pool()
    await close_store()


app = FastAPI(title=settings.APP_NAME, version=settings.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
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


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    keys = settings.api_keys
    if not keys:
        return
    if x_api_key is None or not any(hmac.compare_digest(x_api_key, k) for k in keys):
        raise HTTPException(status_code=401, detail="missing or invalid X-API-Key",
                            headers={"WWW-Authenticate": "ApiKey"})


def _retry_headers(retry_after: float | None) -> dict | None:
    if retry_after is None:
        return None
    return {"Retry-After": str(max(1, math.ceil(retry_after)))}


def _error_response(status: int, code: str, message: str, retry_after: float | None = None,
                    attempts: list[dict] | None = None) -> JSONResponse:
    body = {"detail": message,
            "error": {"code": code, "message": message,
                      "retry_after_s": round(retry_after, 1) if retry_after is not None else None,
                      "request_id": request_id_var.get(),
                      "attempts": attempts or []}}
    return JSONResponse(status_code=status, content=body, headers=_retry_headers(retry_after))


async def _track(request: GatewayRequest, response, status: str, error_type: str | None = None) -> None:
    try:
        await asyncio.wait_for(
            tracker.log(
                request.user_id, request.conversation_id, response, status=status,
                error_type=error_type, cache_scope=request.cache_scope,
                estimated_tokens=estimate_request_tokens(request),
                request_id=request_id_var.get(), attempts=attempt_log.current(),
            ),
            DB_TIMEOUT_SECONDS,
        )
    except Exception as e:
        log.warning("could not persist request log: %s: %s", type(e).__name__, e)


@app.get("/health")
async def health_check():
    try:
        store_ok = await asyncio.wait_for(get_store().ping(), 2.0)
    except Exception:
        store_ok = False
    return {"status": "ok" if store_ok else "degraded",
            "deployments": [d.id for d in get_deployments()],
            "state_store": {"backend": get_store().backend, "ok": store_ok},
            "database": has_pool(),
            "auth": bool(settings.api_keys)}


@app.post("/query", response_model=GatewayResponse, dependencies=[Depends(require_api_key)])
async def handle_query(request: GatewayRequest):
    attempts = attempt_log.start()
    try:
        response = await route(request)
    except InvalidRequestError as e:
        log.info("/query rejected (invalid request): %s", e)
        await _track(request, None, "error", "InvalidRequestError")
        return _error_response(400, "invalid_request", str(e), attempts=attempts.public())
    except TooLongError as e:
        log.info("/query rejected (too long): %s", e)
        await _track(request, None, "error", "TooLongError")
        return _error_response(413, "too_long", str(e))
    except GatewayRejection as e:
        log.warning("/query rejected (%s): %s", e.code, e)
        await _track(request, None, "error", type(e).__name__)
        return _error_response(e.status_code, e.code, str(e), e.retry_after, e.attempts)
    except Exception as e:
        log.error("/query failed: %s", e, exc_info=True)
        await _track(request, None, "error", type(e).__name__)
        return _error_response(500, "internal_error", f"{type(e).__name__}: {e}")

    try:
        response.model_dump(mode="json")
    except Exception as e:
        log.error("/query response is not JSON-serializable: %s: %s", type(e).__name__, e, exc_info=True)
        await _track(request, None, "error", "ResponseSerializationError")
        return _error_response(500, "internal_error", "Gateway produced a response that could not be serialized")

    log.info("/query ok: provider=%s model=%s fallback=%s cache_hit=%s latency_ms=%.0f cost_usd=%.6f",
             response.provider_used, response.model_used, response.was_fallback,
             response.cache_hit, response.latency_ms, response.cost_usd)
    await _track(request, response, "success")
    return response


def _require_db() -> None:
    if not has_pool():
        raise HTTPException(status_code=503, detail="database is not configured")


@app.get("/stats/global", dependencies=[Depends(_require_db)])
async def global_stats():
    return await tracker.get_global_stats()


@app.get("/stats/user/{user_id}", response_model=CostSummary, dependencies=[Depends(_require_db)])
async def user_stats(user_id: str):
    stats = await tracker.get_user_stats(user_id)
    if stats is None:
        raise HTTPException(status_code=404, detail=f"No requests found for user '{user_id}'")
    return stats


@app.get("/stats/requests", dependencies=[Depends(_require_db)])
async def request_history(limit: int = Query(default=200, ge=1, le=MAX_HISTORY_ROWS)):
    return await tracker.get_recent_requests(limit)


@app.get("/experiments")
async def get_experiments():
    return load_runs()


@app.post("/experiments", dependencies=[Depends(require_api_key)])
async def log_experiment(run: ExperimentRun):
    return save_run(run)


@app.get("/status")
async def status():
    return await gateway_status(get_deployments())


def _parse_window(since: str | None, until: str | None):
    try:
        return metrics_store.parse_ts(since), metrics_store.parse_ts(until)
    except ValueError:
        raise HTTPException(status_code=400, detail="since/until must be ISO 8601 timestamps")


@app.get("/metrics/summary", dependencies=[Depends(_require_db)])
async def metrics_summary(since: str | None = None, until: str | None = None):
    s, u = _parse_window(since, until)
    return await metrics_store.get_metrics_summary(s, u)


class SnapshotRequest(BaseModel):
    label: str
    since: str | None = None
    until: str | None = None


@app.get("/metrics/snapshots", dependencies=[Depends(_require_db)])
async def metrics_snapshots():
    return await metrics_store.list_snapshots()


@app.post("/metrics/snapshots", dependencies=[Depends(require_api_key), Depends(_require_db)])
async def metrics_save_snapshot(body: SnapshotRequest):
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    s, u = _parse_window(body.since, body.until)
    return await metrics_store.save_snapshot(label[:120], s, u)
