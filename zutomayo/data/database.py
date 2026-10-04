"""
PostgreSQL connection pool, created at startup (main.py setup_hook) and closed on
shutdown; storage modules use get_pool(). Unit tests swap in in-memory backends;
integration tests create a pool from ZUTOKA_TEST_DATABASE_URL. Configured by
DATABASE_URL, else the standard PG* variables.
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
    """Apply schema.sql; every statement is idempotent (IF NOT EXISTS, ON CONFLICT DO NOTHING)."""
    schema_sql = SCHEMA_FILE.read_text(encoding='utf-8')
    async with get_pool().acquire() as connection:
        await connection.execute(schema_sql)
    log.info('Database schema applied')
