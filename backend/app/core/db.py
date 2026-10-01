import asyncpg
import os

_pool: asyncpg.Pool | None = None

async def init_pool():
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=os.environ["DATABASE_URL"],
        min_size=1, max_size=5,          # small pool — fine at this scale
        ssl="require",                    # Neon requires TLS
    )

async def close_pool():
    if _pool:
        await _pool.close()

def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — did startup run?")
    return _pool