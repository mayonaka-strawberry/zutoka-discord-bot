from __future__ import annotations

import asyncio
import logging
from typing import Callable, Coroutine, Any, TypeVar

import discord

log = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 5

T = TypeVar('T')


async def send_with_retry(coroutine_factory: Callable[[], Coroutine[Any, Any, T]], label: str = 'send') -> T:
    """Call coroutine_factory() and retry up to MAX_SEND_ATTEMPTS times on 5xx errors."""
    for attempt in range(MAX_SEND_ATTEMPTS):
        try:
            return await coroutine_factory()
        except (discord.errors.DiscordServerError, discord.errors.HTTPException) as error:
            is_retryable = (
                isinstance(error, discord.errors.DiscordServerError)
                or (isinstance(error, discord.errors.HTTPException) and error.status >= 500)
            )
            if is_retryable and attempt < MAX_SEND_ATTEMPTS - 1:
                delay = 2 ** attempt  # 1s, 2s, 4s, 8s
                log.warning(
                    '%s failed (attempt %d/%d), retrying in %ds: %s',
                    label, attempt + 1, MAX_SEND_ATTEMPTS, delay, error,
                )
                await asyncio.sleep(delay)
            else:
                raise
