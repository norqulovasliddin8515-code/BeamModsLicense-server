"""
db.py — asyncpg connection pool + thin query helpers (Phase 4).

Replaces the per-call `aiosqlite.connect(DB_PATH)` pattern. Every data module
(database, drm.licenses, payments.core) and the few direct call sites import
from here. One pool is created at startup and shared.

Placeholders are native PostgreSQL ($1, $2, …). Rows come back as asyncpg
Records, which already support both r["col"] and dict(r), so callers that did
`dict(row)` keep working.

Timestamps are stored as TEXT in ISO-8601 UTC (same representation the code
already used via datetime.now(timezone.utc).isoformat()), so no type surprises
and the SQLite→PG transfer copies them verbatim.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


def _dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    # asyncpg wants postgresql://, not SQLAlchemy's postgresql+asyncpg://
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


async def init_pool(dsn: str | None = None, *, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn or _dsn(), min_size=min_size, max_size=max_size, command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() at startup")
    return _pool


# ── query helpers ───────────────────────────────────────────────────────────
async def fetch(sql: str, *args) -> list[asyncpg.Record]:
    async with pool().acquire() as con:
        return await con.fetch(sql, *args)


async def fetchrow(sql: str, *args) -> Optional[asyncpg.Record]:
    async with pool().acquire() as con:
        return await con.fetchrow(sql, *args)


async def fetchval(sql: str, *args) -> Any:
    async with pool().acquire() as con:
        return await con.fetchval(sql, *args)


async def execute(sql: str, *args) -> str:
    """Returns the command tag, e.g. 'UPDATE 3'."""
    async with pool().acquire() as con:
        return await con.execute(sql, *args)


async def execute_count(sql: str, *args) -> int:
    """Rows affected (parsed from the command tag) — the asyncpg analogue of rowcount."""
    tag = await execute(sql, *args)
    try:
        return int(tag.rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        return 0


@asynccontextmanager
async def transaction():
    """Acquire one connection and open a transaction (for multi-statement atomic work)."""
    async with pool().acquire() as con:
        async with con.transaction():
            yield con


def tag_count(tag: str) -> int:
    try:
        return int(tag.rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        return 0
