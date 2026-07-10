"""
PostgreSQL connection pool for the bot.

The pool is created once at startup (ZutokaBot.setup_hook in main.py) and
closed on shutdown. Storage modules obtain it through get_pool(); tests never
initialize a pool and instead swap in in-memory backends at the module level.

Connection configuration comes from DATABASE_URL in the environment (loaded
from .env by main.py). When DATABASE_URL is absent, asyncpg falls back to the
standard PGHOST / PGPORT / PGUSER / PGPASSWORD / PGDATABASE variables.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import asyncpg

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parent / 'schema.sql'

_pool: Optional[asyncpg.Pool] = None


async def _configure_connection(connection: asyncpg.Connection) -> None:
    """Make JSONB columns round-trip as Python dicts and lists."""
    await connection.set_type_codec(
        'jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog',
    )


async def initialize_pool(dsn: Optional[str] = None) -> None:
    global _pool
    if _pool is not None:
        return
    _pool = await asyncpg.create_pool(
        dsn=dsn if dsn is not None else os.environ.get('DATABASE_URL'),
        min_size=1,
        max_size=10,
        init=_configure_connection,
    )
    log.info('PostgreSQL connection pool initialized')


async def close_pool() -> None:
    global _pool
    if _pool is None:
        return
    await _pool.close()
    _pool = None
    log.info('PostgreSQL connection pool closed')


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError(
            'Database pool is not initialized. '
            'initialize_pool() must run before any storage access.'
        )
    return _pool


async def apply_schema() -> None:
    """Apply schema.sql; every statement is idempotent (CREATE ... IF NOT EXISTS)."""
    schema_sql = SCHEMA_FILE.read_text(encoding='utf-8')
    async with get_pool().acquire() as connection:
        await connection.execute(schema_sql)
    log.info('Database schema applied')
