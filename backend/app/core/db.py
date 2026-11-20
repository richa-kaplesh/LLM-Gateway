import os

import asyncpg

_pool: asyncpg.Pool | None = None


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


async def init_pool() -> bool:
    global _pool
    url = database_url()
    if not url:
        return False
    _pool = await asyncpg.create_pool(dsn=url, min_size=1, max_size=5, ssl="require")
    return True


async def close_pool():
    if _pool:
        await _pool.close()


def has_pool() -> bool:
    return _pool is not None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database is not configured (DATABASE_URL is empty)")
    return _pool
