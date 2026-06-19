from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.models.schemas import GatewayRequest, GatewayResponse, CostSummary, HealthCheck
from app.router.router import route
from app.tracker.tracker import tracker
from app.core.config import get_settings
import groq
from google import genai

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION
)

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
        genai.configure(api_key=settings.GEMINI_API_KEY)
    except Exception:
        gemini_available = False

    return HealthCheck(
        status="ok",
        groq_available=groq_available,
        gemini_available=gemini_available
    )


@app.post("/query", response_model=GatewayResponse)
async def handle_query(request: GatewayRequest):
    try:
        response = await route(
            query=request.query,
            user_id=request.user_id
        )

        tracker.log(
            user_id=request.user_id,
            query=request.query,
            response=response
        )

        print("TRACKER LOGS:", len(tracker.logs))
        print("LAST LOG COST:", tracker.logs[-1].cost_usd if tracker.logs else "empty")

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats/global")
async def global_stats():
    return tracker.get_global_stats()


@app.get("/stats/user/{user_id}")
async def user_stats(user_id: str):
    return tracker.get_user_stats(user_id)

@app.get("/stats/requests")
async def request_history():
    return [
        {
            "timestamp": log.timestamp.isoformat(),
            "cost_usd": log.cost_usd,
            "latency_ms": log.latency_ms,
            "model_used": log.model_used,
            "cache_hit": log.cache_hit
        }
        for log in tracker.logs
    ]
